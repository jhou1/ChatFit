import asyncio
from contextlib import closing
import sqlite3
from types import SimpleNamespace
from datetime import date

import httpx
import pytest

from langchain_core.messages import AIMessage

import api as api_module
import evaluation.runner as evaluation_runner
import main as main_module
import proactive_reviews
from agents.memory.agent import LLMMemoryInterpreter
from agents.memory.models import MemoryMutationDecision, MemoryType, NewUserMemory
from agents.memory.store import UserMemoryStore, owner_key_for
from agents.observability import InMemorySink, observation_sink
from agents.models import MealInfo, TrainingInputRecorder, TrainingSession, TrainingSet
from agents.sqlite_handler import add_meal_log, add_training_session, init_db
from evaluation.models import EvaluationCase

TEST_CHATFIT_API_TOKEN = "test-chatfit-api-token"


@pytest.fixture(autouse=True)
def isolate_api_globals():
    """Restore mutable module-level API state after every test."""
    original_state = dict(api_module.app.state._state)
    original_sessions = dict(api_module.user_sessions)
    original_token = api_module.os.environ.get("CHATFIT_API_TOKEN")
    api_module.os.environ["CHATFIT_API_TOKEN"] = TEST_CHATFIT_API_TOKEN
    try:
        yield
    finally:
        api_module.app.state._state.clear()
        api_module.app.state._state.update(original_state)
        api_module.user_sessions.clear()
        api_module.user_sessions.update(original_sessions)
        if original_token is None:
            api_module.os.environ.pop("CHATFIT_API_TOKEN", None)
        else:
            api_module.os.environ["CHATFIT_API_TOKEN"] = original_token


@pytest.fixture
def api_auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_CHATFIT_API_TOKEN}"}


@pytest.fixture
def temp_db_path(tmp_path):
    db_path = tmp_path / "proactive_review.db"
    init_db(db_path)
    return db_path


class FakeAgent:
    def __init__(self) -> None:
        self.config = None
        self.configs: list[dict] = []

    async def aget_state(self, config):
        self.config = config
        self.configs.append(config)
        return SimpleNamespace(tasks=[])

    async def astream(self, action, *, config, stream_mode):
        self.config = config
        self.configs.append(config)
        yield {"chatter": {"messages": [AIMessage(content="backend is ready")]}}


class FakeMemoryRefreshAgent(FakeAgent):
    async def astream(self, action, *, config, stream_mode):
        self.config = config
        self.configs.append(config)
        yield {"memory": {"messages": [AIMessage(content="已记住你的训练偏好。")]}}
        yield {
            "refresh_memories": {
                "messages": [
                    AIMessage(
                        content=(
                            "已记住你的训练偏好。 "
                            "长期记忆操作已完成，但长期记忆上下文暂时无法刷新，请稍后再试。"
                        )
                    )
                ]
            }
        }


class FakeInterruptAgent(FakeAgent):
    async def astream(self, action, *, config, stream_mode):
        self.config = config
        yield {
            "__interrupt__": [
                SimpleNamespace(
                    id="interrupt-123",
                    value={
                        "tool_calls": [
                            {
                                "name": "log_meal",
                                "args": {"items": "private"},
                                "id": "tool-123",
                            }
                        ]
                    },
                )
            ]
        }


class FakeResumeAgent(FakeAgent):
    async def aget_state(self, config):
        self.config = config
        pending = SimpleNamespace(
            id="interrupt-123",
            value={"tool_calls": [{"name": "log_meal", "id": "tool-123"}]},
        )
        return SimpleNamespace(
            tasks=[SimpleNamespace(interrupts=[pending])],
        )

    async def astream(self, action, *, config, stream_mode):
        self.action = action
        async for event in super().astream(
            action, config=config, stream_mode=stream_mode
        ):
            yield event


class FakeParallelInterruptAgent(FakeAgent):
    async def astream(self, action, *, config, stream_mode):
        self.config = config
        yield {
            "__interrupt__": [
                SimpleNamespace(
                    id="interrupt-training",
                    value={
                        "tool_calls": [
                            {"name": "log_training_session", "id": "tool-training"}
                        ]
                    },
                ),
                SimpleNamespace(
                    id="interrupt-meal",
                    value={"tool_calls": [{"name": "log_meal", "id": "tool-meal"}]},
                ),
            ]
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ("/chat", "/clear"),
)
@pytest.mark.parametrize(
    ("authorization", "expected_status", "expected_challenge"),
    (
        (None, 401, "Bearer"),
        ("Basic wrong-secret", 401, "Bearer"),
        ("Bearer", 401, "Bearer"),
        (f"Bearer {TEST_CHATFIT_API_TOKEN} extra", 401, "Bearer"),
        (f"Bearer {TEST_CHATFIT_API_TOKEN}\textra", 401, "Bearer"),
        (f"Bearer {TEST_CHATFIT_API_TOKEN},extra", 401, "Bearer"),
        (f'Bearer "{TEST_CHATFIT_API_TOKEN}"', 401, "Bearer"),
        (b"Bearer\xa0" + TEST_CHATFIT_API_TOKEN.encode("ascii"), 401, "Bearer"),
        (f"Bearer\t{TEST_CHATFIT_API_TOKEN}", 401, "Bearer"),
        (
            (f"Bearer {TEST_CHATFIT_API_TOKEN}", "Bearer extra-credential"),
            401,
            "Bearer",
        ),
        ("Bearer wrong-secret", 403, None),
    ),
)
async def test_untrusted_identity_cannot_access_victim_chat_or_clear_state(
    path,
    authorization,
    expected_status,
    expected_challenge,
    tmp_path,
    caplog,
):
    """Breaks if an untrusted caller can select another user's memory owner."""
    agent = FakeAgent()
    api_module.app.state.agent = agent
    store = UserMemoryStore(tmp_path / "user-memory.db")
    victim_owner = owner_key_for("victim")
    store.remember(
        victim_owner,
        NewUserMemory(
            memory_type=MemoryType.HEALTH_CONSTRAINT,
            canonical_key="private-health",
            display_name="私密健康信息",
            content="victim-private-memory",
        ),
    )
    api_module.app.state.user_memory_store = store
    api_module.user_sessions.clear()
    api_module.user_sessions["victim"] = "victim-thread"

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        if isinstance(authorization, tuple):
            headers = [("Authorization", value) for value in authorization]
        elif isinstance(authorization, bytes):
            headers = [(b"Authorization", authorization)]
        else:
            headers = {"Authorization": authorization} if authorization else {}
        response = await client.post(
            path,
            headers=headers,
            json={"user_id": "victim", "message": "记住攻击者内容"},
        )

    assert response.status_code == expected_status
    assert response.headers.get("WWW-Authenticate") == expected_challenge
    assert api_module.user_sessions == {"victim": "victim-thread"}
    assert agent.configs == []
    assert [memory.content for memory in store.list_memories(victim_owner)] == [
        "victim-private-memory"
    ]
    assert "wrong-secret" not in caplog.text
    assert TEST_CHATFIT_API_TOKEN not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "user_id"),
    (
        ("/chat", "   "),
        ("/chat", "x" * 129),
        ("/clear", "   "),
        ("/clear", "x" * 129),
    ),
)
async def test_invalid_user_identity_is_rejected_before_session_or_graph_access(
    path, user_id, api_auth_headers
):
    """Breaks if blank or unbounded identities can become durable owners."""
    agent = FakeAgent()
    api_module.app.state.agent = agent
    api_module.user_sessions.clear()
    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers=api_auth_headers,
    ) as client:
        response = await client.post(
            path,
            json={"user_id": user_id, "message": "hello"},
        )

    assert response.status_code == 422
    assert api_module.user_sessions == {}
    assert agent.configs == []


@pytest.mark.asyncio
async def test_proactive_review_returns_typed_daily_result_without_thread_changes(
    monkeypatch, temp_db_path
):
    monkeypatch.setattr(
        api_module,
        "today_in_shanghai",
        lambda: date(2026, 7, 31),
        raising=False,
    )
    api_module.app.state.db_path = str(temp_db_path)
    api_module.app.state.llm_config = object()
    api_module.user_sessions.clear()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/proactive-review")

    assert response.status_code == 200
    assert response.json() == {
        "should_send": True,
        "message": "今天还没有看到饮食或训练记录。今天吃了什么，练了什么？",
    }
    assert api_module.user_sessions == {}


@pytest.mark.asyncio
async def test_proactive_review_returns_no_send_when_daily_categories_are_complete(
    monkeypatch, temp_db_path
):
    target_date = date(2026, 7, 31)
    add_meal_log(
        MealInfo(
            date=target_date,
            meal_type="dinner",
            items="米饭和鱼",
            note="训练后",
        ),
        str(temp_db_path),
    )
    add_training_session(
        TrainingInputRecorder(
            date=target_date,
            sessions=[
                TrainingSession(
                    practice_name="深蹲",
                    practice_type="weighted",
                    rpe=7,
                    note="状态舒适",
                    sets=[TrainingSet(set_number=1, weight=100, reps=5)],
                )
            ],
            confirm_new_practices=True,
        ),
        str(temp_db_path),
    )
    monkeypatch.setattr(
        api_module, "today_in_shanghai", lambda: target_date, raising=False
    )
    api_module.app.state.db_path = str(temp_db_path)
    api_module.app.state.llm_config = object()
    api_module.user_sessions.clear()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/proactive-review")

    assert response.status_code == 200
    assert response.json() == {"should_send": False, "message": None}
    assert api_module.user_sessions == {}


@pytest.mark.asyncio
async def test_proactive_review_uses_api_state_for_saturday_weekly_summary(
    monkeypatch, temp_db_path
):
    target_date = date(2026, 8, 1)
    llm_config = object()
    add_meal_log(
        MealInfo(
            date=target_date,
            meal_type="dinner",
            items="米饭和鱼",
            note="训练后",
        ),
        str(temp_db_path),
    )
    add_training_session(
        TrainingInputRecorder(
            date=target_date,
            sessions=[
                TrainingSession(
                    practice_name="深蹲",
                    practice_type="weighted",
                    rpe=7,
                    note="状态舒适",
                    sets=[TrainingSet(set_number=1, weight=100, reps=5)],
                )
            ],
            confirm_new_practices=True,
        ),
        str(temp_db_path),
    )

    async def fake_weekly_insights(config, db_path, start_date, end_date):
        assert config is llm_config
        assert db_path == str(temp_db_path)
        assert (start_date, end_date) == (date(2026, 7, 26), target_date)
        return "## 本周总结\n\n本周训练和饮食保持稳定。"

    monkeypatch.setattr(
        api_module, "today_in_shanghai", lambda: target_date, raising=False
    )
    monkeypatch.setattr(
        api_module,
        "generate_weekly_insights",
        fake_weekly_insights,
        raising=False,
    )
    api_module.app.state.db_path = str(temp_db_path)
    api_module.app.state.llm_config = llm_config
    api_module.user_sessions.clear()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/proactive-review")

    assert response.status_code == 200
    assert response.json() == {
        "should_send": True,
        "message": "## 本周总结\n\n本周训练和饮食保持稳定。",
    }
    assert api_module.user_sessions == {}


@pytest.mark.asyncio
async def test_proactive_review_weekly_timeout_returns_daily_question(
    monkeypatch: pytest.MonkeyPatch,
    temp_db_path,
):
    """Breaks if API-level weekly work can outlive its end-to-end deadline."""
    target_date = date(2026, 8, 1)
    calls = 0

    async def slow_weekly_insights(config, db_path, start_date, end_date):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return "private summary returned after the deadline"

    monkeypatch.setattr(api_module, "today_in_shanghai", lambda: target_date)
    monkeypatch.setattr(api_module, "generate_weekly_insights", slow_weekly_insights)
    monkeypatch.setattr(
        proactive_reviews, "WEEKLY_INSIGHTS_TIMEOUT_SECONDS", 0.0, raising=False
    )
    api_module.app.state.db_path = str(temp_db_path)
    api_module.app.state.llm_config = object()
    api_module.user_sessions.clear()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/proactive-review")

    assert response.status_code == 200
    assert response.json() == {
        "should_send": True,
        "message": (
            "## 本周总结\n\n"
            "本周总结暂时生成失败。你可以稍后发消息让我重新总结。"
            "\n\n---\n\n"
            "今天还没有看到饮食或训练记录。今天吃了什么，练了什么？"
        ),
    }
    assert calls == 1
    assert api_module.user_sessions == {}


@pytest.mark.asyncio
async def test_empty_chat_response_preserves_correlation_headers(api_auth_headers):
    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/chat",
            headers={**api_auth_headers, "X-Request-ID": "empty-message-request"},
            json={"user_id": "test-user", "message": "   "},
        )

    assert response.status_code == 400
    assert response.headers["X-Request-ID"] == "empty-message-request"
    assert response.headers["X-Trace-ID"]


@pytest.mark.asyncio
async def test_chat_uses_langfuse_v4_callback_without_removed_host_argument(
    monkeypatch, api_auth_headers
):
    callback = object()
    callback_kwargs = {}

    def callback_factory(**kwargs):
        callback_kwargs.update(kwargs)
        return callback

    monkeypatch.setattr(api_module, "CallbackHandler", callback_factory)
    monkeypatch.setenv("LANGFUSE_HOST", "https://configured-by-environment.example")
    monkeypatch.setenv("OBSERVABILITY_HASH_KEY", "test-hash-key")
    agent = FakeAgent()
    api_module.app.state.agent = agent
    api_module.user_sessions.clear()

    transport = httpx.ASGITransport(
        app=api_module.app,
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=api_auth_headers
    ) as client:
        response = await client.post(
            "/chat", json={"user_id": "test-user", "message": "hello"}
        )

    assert response.status_code == 200
    assert response.json() == {"response": "backend is ready", "pending_tools": None}
    assert agent.config["callbacks"] == [callback]
    assert "host" not in callback_kwargs
    assert (
        callback_kwargs["trace_context"]["trace_id"] == response.headers["X-Trace-ID"]
    )
    assert response.headers["X-Request-ID"]
    assert agent.config["metadata"]["user_key"] != "test-user"
    assert "user_id" not in agent.config["metadata"]


@pytest.mark.asyncio
async def test_chat_remains_available_when_langfuse_callback_initialization_fails(
    monkeypatch, api_auth_headers
):
    def incompatible_callback_factory(**kwargs):
        raise TypeError("unexpected callback constructor argument")

    monkeypatch.setattr(api_module, "CallbackHandler", incompatible_callback_factory)
    agent = FakeAgent()
    api_module.app.state.agent = agent
    api_module.user_sessions.clear()

    transport = httpx.ASGITransport(
        app=api_module.app,
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=api_auth_headers
    ) as client:
        response = await client.post(
            "/chat", json={"user_id": "test-user", "message": "hello"}
        )

    assert response.status_code == 200
    assert response.json()["response"] == "backend is ready"
    assert agent.config["callbacks"] == []


@pytest.mark.asyncio
async def test_chat_returns_only_final_memory_refresh_response(
    monkeypatch, api_auth_headers
):
    """Breaks if the API streams a provisional Memory reply or misses its warning."""
    monkeypatch.setattr(api_module, "CallbackHandler", lambda **kwargs: object())
    agent = FakeMemoryRefreshAgent()
    api_module.app.state.agent = agent
    api_module.user_sessions.clear()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers=api_auth_headers,
    ) as client:
        response = await client.post(
            "/chat", json={"user_id": "test-user", "message": "记住训练偏好"}
        )

    assert response.status_code == 200
    response_text = response.json()["response"]
    assert response_text.count("已记住你的训练偏好。") == 1
    assert "长期记忆上下文暂时无法刷新" in response_text


@pytest.mark.asyncio
async def test_chat_interrupt_trace_contains_interrupt_id_and_interrupted_status(
    monkeypatch, api_auth_headers
):
    async def approval_message(tool_calls, llm_config):
        return "Please approve"

    monkeypatch.setattr(api_module, "CallbackHandler", lambda **kwargs: object())
    monkeypatch.setattr(
        api_module, "generate_conversational_approval", approval_message
    )
    agent = FakeInterruptAgent()
    api_module.app.state.agent = agent
    api_module.app.state.llm_config = object()
    api_module.user_sessions.clear()
    sink = InMemorySink()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    with observation_sink(sink):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=api_auth_headers,
        ) as client:
            response = await client.post(
                "/chat", json={"user_id": "test-user", "message": "save lunch"}
            )

    assert response.status_code == 200
    requested = next(
        observation
        for observation in sink.observations
        if observation.name == "hitl.requested"
    )
    assert requested.attributes["interrupt.ids"] == ["interrupt-123"]
    ended = {
        observation.name: observation.status
        for observation in sink.observations
        if observation.signal == "span.end"
    }
    assert ended["graph.run"] == "interrupted"
    assert ended["chat.request"] == "interrupted"


@pytest.mark.asyncio
async def test_chat_passes_complete_revision_reply_to_pending_interrupt(
    monkeypatch, api_auth_headers
):
    monkeypatch.setattr(api_module, "CallbackHandler", lambda **kwargs: object())
    agent = FakeResumeAgent()
    api_module.app.state.agent = agent
    api_module.app.state.llm_config = object()
    api_module.user_sessions.clear()
    sink = InMemorySink()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    with observation_sink(sink):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=api_auth_headers,
        ) as client:
            response = await client.post(
                "/chat",
                json={"user_id": "test-user", "message": "保存，同时 RPE 7"},
            )

    assert response.status_code == 200
    assert agent.action.resume == {
        "interrupt-123": {"user_message": "保存，同时 RPE 7"}
    }
    reply_received = next(
        observation
        for observation in sink.observations
        if observation.name == "hitl.reply_received"
    )
    assert reply_received.attributes == {
        "interrupt.count": 1,
        "interrupt.ids": ["interrupt-123"],
    }


@pytest.mark.asyncio
async def test_chat_flattens_all_parallel_interrupt_tool_calls(
    monkeypatch, api_auth_headers
):
    captured_tool_calls = []

    async def approval_message(tool_calls, llm_config):
        captured_tool_calls.extend(tool_calls)
        return "Please approve both"

    monkeypatch.setattr(api_module, "CallbackHandler", lambda **kwargs: object())
    monkeypatch.setattr(
        api_module, "generate_conversational_approval", approval_message
    )
    agent = FakeParallelInterruptAgent()
    api_module.app.state.agent = agent
    api_module.app.state.llm_config = object()
    api_module.user_sessions.clear()
    sink = InMemorySink()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    with observation_sink(sink):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=api_auth_headers,
        ) as client:
            response = await client.post(
                "/chat",
                json={"user_id": "test-user", "message": "save workout and meal"},
            )

    assert response.status_code == 200
    assert [call["name"] for call in captured_tool_calls] == [
        "log_training_session",
        "log_meal",
    ]
    requested = next(
        observation
        for observation in sink.observations
        if observation.name == "hitl.requested"
    )
    assert requested.attributes["interrupt.ids"] == [
        "interrupt-training",
        "interrupt-meal",
    ]
    assert requested.attributes["tool.count"] == 2
    assert requested.attributes["tool.names"] == [
        "log_training_session",
        "log_meal",
    ]


def test_checkpointer_path_creates_parent_directory(monkeypatch, tmp_path):
    checkpointer_path = tmp_path / "runtime-data" / "checkpointer.db"
    monkeypatch.setenv("CHECKPOINTER_DB_PATH", str(checkpointer_path))

    resolved = api_module.get_checkpointer_db_path()

    assert resolved == str(checkpointer_path)
    assert checkpointer_path.parent.is_dir()
    assert not checkpointer_path.exists()


def test_checkpointer_path_rejects_directory_mount(monkeypatch, tmp_path):
    invalid_path = tmp_path / "checkpointer.db"
    invalid_path.mkdir()
    monkeypatch.setenv("CHECKPOINTER_DB_PATH", str(invalid_path))

    with pytest.raises(RuntimeError, match="must be a file path"):
        api_module.get_checkpointer_db_path()


def test_user_memory_path_defaults_without_reusing_other_databases(
    monkeypatch, tmp_path
):
    """Breaks if durable memory silently falls back to another SQLite database."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("USER_MEMORY_DB_PATH", raising=False)
    monkeypatch.setenv("CHECKPOINTER_DB_PATH", "checkpointer.db")

    memory_path = api_module.get_user_memory_db_path()

    assert memory_path == str(tmp_path / "user-memory.db")
    assert memory_path != api_module.get_checkpointer_db_path()
    assert memory_path != str(tmp_path / "business.db")
    assert not (tmp_path / memory_path).exists()


def test_user_memory_path_creates_parent_directory(monkeypatch, tmp_path):
    """Breaks if a configured bind-mount parent is not prepared before SQLite."""
    memory_path = tmp_path / "runtime-data" / "user-memory.db"
    monkeypatch.setenv("USER_MEMORY_DB_PATH", str(memory_path))

    resolved = api_module.get_user_memory_db_path()

    assert resolved == str(memory_path)
    assert memory_path.parent.is_dir()
    assert not memory_path.exists()


def test_user_memory_path_rejects_directory_mount(monkeypatch, tmp_path):
    """Breaks if SQLite is handed a directory-shaped container bind mount."""
    invalid_path = tmp_path / "user-memory.db"
    invalid_path.mkdir()
    monkeypatch.setenv("USER_MEMORY_DB_PATH", str(invalid_path))

    with pytest.raises(RuntimeError, match="must be a file path"):
        api_module.get_user_memory_db_path()


@pytest.mark.parametrize(
    "memory_path",
    (
        ":memory:",
        "file::memory:?cache=shared",
        "file:chatfit-memory?mode=memory&cache=shared",
        "file:/tmp/user-memory.db?mode=rwc",
        "FiLe:user-memory.db",
        "FILE:%2Ftmp%2Fuser-memory.db?mode=rwc",
    ),
)
def test_user_memory_path_rejects_sqlite_memory_and_file_uri_targets(
    monkeypatch, tmp_path, memory_path
):
    """Breaks if a URI is treated as a local path without SQLite URI support."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USER_MEMORY_DB_PATH", memory_path)

    with pytest.raises(RuntimeError, match="SQLite"):
        api_module.get_user_memory_db_path()

    assert not (tmp_path / "file:").exists()


def test_user_memory_path_allows_colon_outside_explicit_file_scheme(
    monkeypatch, tmp_path
):
    """Breaks if URI rejection incorrectly bans legal filesystem colons."""
    memory_path = tmp_path / "archive:2026" / "user-memory.db"
    monkeypatch.setenv("USER_MEMORY_DB_PATH", str(memory_path))

    resolved = api_module.get_user_memory_db_path()

    assert resolved == str(memory_path)
    assert memory_path.parent.is_dir()


def _patch_api_startup_for_path_validation(monkeypatch, business_path):
    original_resolver = api_module.resolve_sqlite_file_path

    def resolve_test_path(value, *, setting_name):
        if value == "~/.iron/iron.db":
            value = business_path
        return original_resolver(value, setting_name=setting_name)

    monkeypatch.setattr(api_module, "resolve_sqlite_file_path", resolve_test_path)
    monkeypatch.setattr(
        api_module,
        "create_langfuse_client",
        lambda: (_ for _ in ()).throw(
            AssertionError("Langfuse initialized before database validation")
        ),
    )
    monkeypatch.setattr(
        api_module,
        "get_or_create_vector_store",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("external setup ran before database validation")
        ),
    )
    monkeypatch.setattr(
        api_module,
        "UserMemoryStore",
        lambda path: (_ for _ in ()).throw(
            AssertionError("memory store initialized before collision validation")
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("collision_kind", ("same", "relative_absolute", "symlink"))
async def test_api_startup_rejects_physical_database_aliases_before_store_init(
    monkeypatch, tmp_path, collision_kind
):
    """Breaks if business, checkpoint, and memory schemas can share one file."""
    monkeypatch.chdir(tmp_path)
    business_path = tmp_path / "business.db"
    business_path.touch()
    checkpointer_path = tmp_path / "checkpointer.db"
    if collision_kind == "same":
        memory_path = str(business_path)
    elif collision_kind == "relative_absolute":
        checkpointer_path = tmp_path / "shared.db"
        memory_path = "shared.db"
    else:
        memory_link = tmp_path / "memory-link.db"
        try:
            memory_link.symlink_to(business_path)
        except OSError as error:
            pytest.skip(f"symlinks unavailable: {error}")
        memory_path = str(memory_link)

    monkeypatch.setenv("CHECKPOINTER_DB_PATH", str(checkpointer_path))
    monkeypatch.setenv("USER_MEMORY_DB_PATH", memory_path)
    _patch_api_startup_for_path_validation(monkeypatch, business_path)

    with pytest.raises(RuntimeError, match="distinct physical files"):
        async with api_module.app.router.lifespan_context(api_module.app):
            pass


@pytest.mark.asyncio
async def test_main_rejects_business_and_memory_database_collision_before_setup(
    monkeypatch, tmp_path
):
    """Breaks if CLI user memory can be mixed into the business database."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USER_MEMORY_DB_PATH", str(tmp_path / "chatfit.db"))
    monkeypatch.setattr(
        main_module,
        "get_or_create_vector_store",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("external setup ran before database validation")
        ),
    )

    with pytest.raises(RuntimeError, match="distinct physical files"):
        await main_module.main()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "memory_path",
    (
        "file:user-memory.db",
        "FiLe:%2Ftmp%2Fuser-memory.db?mode=rwc",
    ),
)
async def test_main_rejects_sqlite_file_uri_before_setup(
    monkeypatch, tmp_path, memory_path
):
    """Breaks if CLI constructs dependencies from an unsupported SQLite URI."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USER_MEMORY_DB_PATH", memory_path)
    monkeypatch.setattr(
        main_module,
        "get_or_create_vector_store",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("external setup ran before URI validation")
        ),
    )

    with pytest.raises(RuntimeError, match="SQLite"):
        await main_module.main()

    assert not (tmp_path / "file:").exists()


@pytest.mark.asyncio
async def test_chat_configurable_user_and_thread_ids_are_stable_per_request_owner(
    monkeypatch, api_auth_headers
):
    """Breaks if memory ownership is omitted, hashed, or tied to one request."""
    monkeypatch.setattr(api_module, "CallbackHandler", lambda **kwargs: object())
    agent = FakeAgent()
    api_module.app.state.agent = agent
    api_module.user_sessions.clear()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=api_auth_headers
    ) as client:
        first = await client.post(
            "/chat", json={"user_id": "  stable-user  ", "message": "hello"}
        )
        second = await client.post(
            "/chat", json={"user_id": "stable-user", "message": "hello again"}
        )

    assert first.status_code == second.status_code == 200
    configurable = [config["configurable"] for config in agent.configs]
    assert {item["user_id"] for item in configurable} == {"stable-user"}
    assert len({item["thread_id"] for item in configurable}) == 1


@pytest.mark.asyncio
async def test_clear_rotates_only_conversation_thread_and_preserves_memory(
    monkeypatch, tmp_path, api_auth_headers
):
    """Breaks if clearing short-term chat state deletes durable user memory."""
    store = UserMemoryStore(tmp_path / "user-memory.db")
    owner_key = owner_key_for("clear-user")
    store.remember(
        owner_key,
        NewUserMemory(
            memory_type=MemoryType.HEALTH_CONSTRAINT,
            canonical_key="lactose",
            display_name="乳糖不耐受",
            content="我乳糖不耐受",
        ),
    )
    monkeypatch.setattr(api_module.app.state, "user_memory_store", store, raising=False)
    api_module.user_sessions.clear()
    old_thread = api_module.get_thread_id("clear-user")

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=api_auth_headers
    ) as client:
        response = await client.post(
            "/clear", json={"user_id": "clear-user", "message": "clear"}
        )

    assert response.status_code == 200
    assert api_module.get_thread_id("clear-user") != old_thread
    assert [memory.content for memory in store.list_memories(owner_key)] == [
        "我乳糖不耐受"
    ]


class _StructuredMemoryRunnable:
    async def ainvoke(self, messages):
        del messages
        return MemoryMutationDecision(
            intent="remember",
            memory_type=MemoryType.HEALTH_CONSTRAINT,
            canonical_key="乳糖不耐受",
            display_name="乳糖不耐受",
            content="模型不得覆盖明确记忆",
        )


class _StructuredMemoryModel:
    def with_structured_output(self, schema):
        assert schema is MemoryMutationDecision
        return _StructuredMemoryRunnable()


class _UnusedSubgraph:
    async def ainvoke(self, state):
        raise AssertionError(f"unexpected specialist invocation: {state!r}")


def _patch_api_lifespan_dependencies(monkeypatch, tmp_path):
    business_path = tmp_path / "business.db"
    checkpointer_path = tmp_path / "checkpointer.db"
    memory_path = tmp_path / "user-memory.db"
    original_resolver = api_module.resolve_sqlite_file_path

    def resolve_test_path(value, *, setting_name):
        if value == "~/.iron/iron.db":
            value = business_path
        return original_resolver(value, setting_name=setting_name)

    monkeypatch.setattr(api_module, "resolve_sqlite_file_path", resolve_test_path)
    monkeypatch.setenv("CHECKPOINTER_DB_PATH", str(checkpointer_path))
    monkeypatch.setenv("USER_MEMORY_DB_PATH", str(memory_path))
    monkeypatch.setattr(api_module, "create_langfuse_client", lambda: None)
    monkeypatch.setattr(api_module, "create_langfuse_callback", lambda trace_id: None)
    monkeypatch.setattr(
        api_module, "get_or_create_vector_store", lambda *args: object()
    )
    monkeypatch.setattr(
        "agents.memory.agent.create_chat_model", lambda config: _StructuredMemoryModel()
    )
    unused = _UnusedSubgraph()
    monkeypatch.setattr(
        "agents.roles.supervisor.make_training_agent_graph",
        lambda *args, **kwargs: unused,
    )
    monkeypatch.setattr(
        "agents.roles.supervisor.make_meal_subagent_graph",
        lambda *args, **kwargs: unused,
    )
    monkeypatch.setattr(
        "agents.roles.supervisor.make_insights_agent_graph",
        lambda *args, **kwargs: unused,
    )
    monkeypatch.setattr(
        "agents.roles.supervisor.create_chat_model", lambda config: object()
    )

    async def deterministic_graph_llm(llm, messages):
        del llm
        if "assigning user input" in str(messages[0].content):
            return {"messages": AIMessage(content="chatter")}
        return {"messages": AIMessage(content=str(messages[0].content))}

    monkeypatch.setattr(
        "agents.roles.supervisor._execute_llm_query_safely",
        deterministic_graph_llm,
    )
    return business_path, checkpointer_path, memory_path


@pytest.mark.asyncio
async def test_memory_survives_full_api_restart_new_thread_and_stays_user_isolated(
    monkeypatch, tmp_path, api_auth_headers
):
    """Breaks if API startup uses transient, shared, or thread-owned memory."""
    business_path, checkpointer_path, memory_path = _patch_api_lifespan_dependencies(
        monkeypatch, tmp_path
    )
    api_module.user_sessions.clear()

    async with api_module.app.router.lifespan_context(api_module.app):
        assert isinstance(api_module.app.state.user_memory_store, UserMemoryStore)
        assert isinstance(api_module.app.state.memory_interpreter, LLMMemoryInterpreter)
        assert api_module.app.state.user_memory_store.db_path == memory_path
        transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=api_auth_headers,
        ) as client:
            remembered = await client.post(
                "/chat",
                json={"user_id": "persistent-user", "message": "记住我乳糖不耐受"},
            )
            original_thread = api_module.get_thread_id("persistent-user")
            cleared = await client.post(
                "/clear",
                json={"user_id": "persistent-user", "message": "clear"},
            )

        assert remembered.status_code == cleared.status_code == 200
        assert "已记住" in remembered.json()["response"]
        assert api_module.get_thread_id("persistent-user") != original_thread

    async with api_module.app.router.lifespan_context(api_module.app):
        assert api_module.app.state.user_memory_store.db_path == memory_path
        transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=api_auth_headers,
        ) as client:
            same_user = await client.post(
                "/chat", json={"user_id": "persistent-user", "message": "你好"}
            )
            other_user = await client.post(
                "/chat", json={"user_id": "other-user", "message": "你好"}
            )

    assert same_user.status_code == other_user.status_code == 200
    assert "我乳糖不耐受" in same_user.json()["response"]
    assert "我乳糖不耐受" not in other_user.json()["response"]
    with closing(sqlite3.connect(memory_path)) as connection:
        memory_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    with closing(sqlite3.connect(business_path)) as connection:
        business_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    with closing(sqlite3.connect(checkpointer_path)) as connection:
        checkpointer_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "user_memories" in memory_tables
    assert "user_memories" not in business_tables
    assert "user_memories" not in checkpointer_tables


@pytest.mark.asyncio
async def test_main_uses_configured_memory_store_and_local_cli_owner(
    monkeypatch, tmp_path, capsys
):
    """Breaks if CLI memory is transient or owned by its random thread ID."""
    monkeypatch.chdir(tmp_path)
    memory_path = tmp_path / "runtime-data" / "cli-user-memory.db"
    monkeypatch.setenv("USER_MEMORY_DB_PATH", str(memory_path))
    monkeypatch.setattr(
        main_module, "get_or_create_vector_store", lambda *args: object()
    )
    monkeypatch.setattr(
        "agents.memory.agent.create_chat_model", lambda config: _StructuredMemoryModel()
    )
    prompts = iter(("hello", "quit"))
    monkeypatch.setattr(
        main_module.Prompt, "ask", lambda *args, **kwargs: next(prompts)
    )
    captured = {}

    class RecordingCliGraph:
        async def astream(self, state, *, config, stream_mode):
            captured["config"] = config
            yield {"memory": {"messages": [AIMessage(content="已记住你的训练偏好。")]}}
            yield {
                "refresh_memories": {
                    "messages": [AIMessage(content="已记住你的最终训练偏好。")]
                }
            }

    def graph_factory(*args, **kwargs):
        captured["graph_kwargs"] = kwargs
        return RecordingCliGraph()

    monkeypatch.setattr(main_module, "make_agent_graph", graph_factory)

    await main_module.main()

    store = captured["graph_kwargs"]["memory_store"]
    assert isinstance(store, UserMemoryStore)
    assert store.db_path == memory_path
    assert captured["config"]["configurable"]["user_id"] == "local-cli"
    assert captured["config"]["configurable"]["thread_id"]
    assert memory_path != tmp_path / "chatfit.db"
    output = capsys.readouterr().out
    assert "已记住你的最终训练偏好。" in output
    assert "已记住你的训练偏好。" not in output


@pytest.mark.asyncio
async def test_evaluation_memory_is_shared_within_case_and_isolated_between_cases(
    monkeypatch,
):
    """Breaks if evaluation cases reuse memory or turns change owner identity."""
    monkeypatch.setattr(
        "agents.memory.agent.create_chat_model", lambda config: _StructuredMemoryModel()
    )
    recordings = []

    class RecordingEvaluationGraph:
        def __init__(self, graph_record):
            self.graph_record = graph_record

        async def astream(self, state, *, config, stream_mode):
            self.graph_record["configs"].append(config)
            if False:
                yield state, stream_mode

        async def aget_state(self, config):
            return SimpleNamespace(next=(), tasks=[])

    def graph_factory(llm_config, db_path, vector_store, checkpointer, **kwargs):
        store = kwargs["memory_store"]
        record = {
            "business_path": db_path,
            "memory_path": str(store.db_path),
            "memory_exists": store.db_path.is_file(),
            "memory_interpreter": kwargs["memory_interpreter"],
            "configs": [],
        }
        recordings.append(record)
        return RecordingEvaluationGraph(record)

    monkeypatch.setattr(evaluation_runner, "make_agent_graph", graph_factory)
    cases = (
        EvaluationCase.model_validate(
            {
                "case_id": "case-a",
                "turns": [{"user_input": "first"}, {"user_input": "second"}],
            }
        ),
        EvaluationCase.model_validate(
            {"case_id": "case-b", "turns": [{"user_input": "only"}]}
        ),
    )

    await asyncio.gather(
        *(
            evaluation_runner.evaluate_case(
                case,
                object(),
                object(),
                asyncio.Semaphore(2),
                False,
            )
            for case in cases
        )
    )

    by_user_id = {
        record["configs"][0]["configurable"]["user_id"]: record for record in recordings
    }
    assert set(by_user_id) == {"case-a", "case-b"}
    assert len(by_user_id["case-a"]["configs"]) == 2
    assert all(
        config["configurable"] == {"thread_id": "case-a", "user_id": "case-a"}
        for config in by_user_id["case-a"]["configs"]
    )
    assert all(record["memory_exists"] for record in recordings)
    assert all(
        isinstance(record["memory_interpreter"], LLMMemoryInterpreter)
        for record in recordings
    )
    assert len({record["memory_path"] for record in recordings}) == 2
    assert all(
        record["memory_path"] != record["business_path"] for record in recordings
    )


@pytest.mark.asyncio
async def test_evaluation_grades_only_final_memory_refresh_response(monkeypatch):
    """Breaks if evaluation grades both provisional and finalized Memory replies."""
    captured_trajectories = []
    monkeypatch.setattr(
        "agents.memory.agent.create_chat_model", lambda config: _StructuredMemoryModel()
    )

    class FinalizedMemoryEvaluationGraph:
        async def astream(self, state, *, config, stream_mode):
            del state, config, stream_mode
            yield {"memory": {"messages": [AIMessage(content="已记住你的训练偏好。")]}}
            yield {
                "refresh_memories": {
                    "messages": [
                        AIMessage(
                            content=(
                                "已记住你的训练偏好。 "
                                "长期记忆操作已完成，但长期记忆上下文暂时无法刷新，请稍后再试。"
                            )
                        )
                    ]
                }
            }

        async def aget_state(self, config):
            del config
            return SimpleNamespace(next=(), tasks=[])

    def graph_factory(*args, **kwargs):
        del args, kwargs
        return FinalizedMemoryEvaluationGraph()

    def capture_grade(turn, trajectory):
        del turn
        captured_trajectories.append(trajectory)
        return SimpleNamespace(passed=True, failures=[])

    monkeypatch.setattr(evaluation_runner, "make_agent_graph", graph_factory)
    monkeypatch.setattr(evaluation_runner, "grade_turn", capture_grade)
    case = EvaluationCase.model_validate(
        {"case_id": "memory-stream", "turns": [{"user_input": "记住训练偏好"}]}
    )

    result = await evaluation_runner.evaluate_case(
        case,
        object(),
        object(),
        asyncio.Semaphore(1),
        False,
    )

    response_text = captured_trajectories[0].response
    assert result.passed is True
    assert response_text.count("已记住你的训练偏好。") == 1
    assert "长期记忆上下文暂时无法刷新" in response_text


def test_langfuse_content_is_redacted_by_default_and_requires_explicit_opt_in(
    monkeypatch,
):
    sensitive = {"message": "private health data"}
    monkeypatch.delenv("LANGFUSE_CAPTURE_CONTENT", raising=False)

    assert api_module.mask_langfuse_content(data=sensitive) == "[REDACTED]"

    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "true")
    assert api_module.mask_langfuse_content(data=sensitive) is sensitive


def test_langfuse_client_initialization_is_fail_open(monkeypatch):
    def fail_to_initialize(**kwargs):
        raise RuntimeError("telemetry backend unavailable")

    monkeypatch.setattr(api_module, "Langfuse", fail_to_initialize)

    assert api_module.create_langfuse_client() is None


def test_langfuse_client_receives_default_content_mask(monkeypatch):
    captured = {}

    class FakeLangfuse:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(api_module, "Langfuse", FakeLangfuse)

    client = api_module.create_langfuse_client()

    assert client is not None
    assert captured["mask"] is api_module.mask_langfuse_content
