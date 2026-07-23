from types import SimpleNamespace

import httpx
import pytest

from langchain_core.messages import AIMessage

import api as api_module


class FakeAgent:
    def __init__(self) -> None:
        self.config = None

    async def aget_state(self, config):
        self.config = config
        return SimpleNamespace(tasks=[])

    async def astream(self, action, *, config, stream_mode):
        self.config = config
        yield {"chatter": {"messages": [AIMessage(content="backend is ready")]}}


@pytest.mark.asyncio
async def test_chat_uses_langfuse_v4_callback_without_removed_host_argument(
    monkeypatch,
):
    callback = object()

    def callback_factory():
        return callback

    monkeypatch.setattr(api_module, "CallbackHandler", callback_factory)
    monkeypatch.setenv("LANGFUSE_HOST", "https://configured-by-environment.example")
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


@pytest.mark.asyncio
async def test_chat_remains_available_when_langfuse_callback_initialization_fails(
    monkeypatch,
):
    def incompatible_callback_factory():
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
