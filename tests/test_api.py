from types import SimpleNamespace

import httpx
import pytest

from langchain_core.messages import AIMessage

import api as api_module
from agents.observability import InMemorySink, observation_sink


class FakeAgent:
    def __init__(self) -> None:
        self.config = None

    async def aget_state(self, config):
        self.config = config
        return SimpleNamespace(tasks=[])

    async def astream(self, action, *, config, stream_mode):
        self.config = config
        yield {"chatter": {"messages": [AIMessage(content="backend is ready")]}}


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
async def test_empty_chat_response_preserves_correlation_headers():
    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/chat",
            headers={"X-Request-ID": "empty-message-request"},
            json={"user_id": "test-user", "message": "   "},
        )

    assert response.status_code == 400
    assert response.headers["X-Request-ID"] == "empty-message-request"
    assert response.headers["X-Trace-ID"]


@pytest.mark.asyncio
async def test_chat_uses_langfuse_v4_callback_without_removed_host_argument(
    monkeypatch,
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
        transport=transport, base_url="http://testserver"
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
    monkeypatch,
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
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/chat", json={"user_id": "test-user", "message": "hello"}
        )

    assert response.status_code == 200
    assert response.json()["response"] == "backend is ready"
    assert agent.config["callbacks"] == []


@pytest.mark.asyncio
async def test_chat_interrupt_trace_contains_interrupt_id_and_interrupted_status(
    monkeypatch,
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
            transport=transport, base_url="http://testserver"
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
async def test_chat_resume_trace_reuses_interrupt_id(monkeypatch):
    async def approved_intent(message, llm_config):
        return True, message

    monkeypatch.setattr(api_module, "CallbackHandler", lambda **kwargs: object())
    monkeypatch.setattr(api_module, "_classify_approval_intent", approved_intent)
    agent = FakeResumeAgent()
    api_module.app.state.agent = agent
    api_module.app.state.llm_config = object()
    api_module.user_sessions.clear()
    sink = InMemorySink()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    with observation_sink(sink):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/chat", json={"user_id": "test-user", "message": "yes"}
            )

    assert response.status_code == 200
    resumed = next(
        observation
        for observation in sink.observations
        if observation.name == "hitl.resumed"
    )
    assert resumed.attributes == {
        "decision": "approved",
        "interrupt.count": 1,
        "interrupt.ids": ["interrupt-123"],
    }


@pytest.mark.asyncio
async def test_chat_flattens_all_parallel_interrupt_tool_calls(monkeypatch):
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
            transport=transport, base_url="http://testserver"
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
