from collections.abc import Iterator
import importlib
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.llm_factory import LLMConfig
from agents.memory.models import (
    MemoryMutationDecision,
    MemoryType,
    NewUserMemory,
    PendingMemoryAction,
)
from agents.memory.store import UserMemoryStore, owner_key_for
from agents.roles.supervisor import make_agent_graph, route_assistant_on_relevance


class DeterministicInterpreter:
    """Return literal decisions without reaching an external model."""

    def __init__(self, *decisions: MemoryMutationDecision) -> None:
        self._decisions: Iterator[MemoryMutationDecision] = iter(decisions)

    async def interpret(self, *, user_message, memories, pending):
        del user_message, memories, pending
        return next(self._decisions)


class _UnusedSubgraph:
    async def ainvoke(self, state):
        raise AssertionError(f"unexpected subagent invocation: {state!r}")


def _llm_config() -> LLMConfig:
    return LLMConfig(provider="google", model_name="unused", kwargs={})


def _remember_lactose_decision() -> MemoryMutationDecision:
    return MemoryMutationDecision(
        intent="remember",
        memory_type=MemoryType.HEALTH_CONSTRAINT,
        canonical_key="乳糖不耐受",
        display_name="乳糖不耐受",
        content="模型不得覆盖的内容",
        aliases=("不耐乳糖",),
    )


def _build_graph(monkeypatch, store, interpreter):
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
        "agents.roles.supervisor.create_chat_model",
        lambda config: object(),
    )

    async def chatter_response(llm, messages):
        del llm
        if "assigning user input" in str(messages[0].content):
            routed = (
                "memory_agent" if "记住" in str(messages[-1].content) else "chatter"
            )
            return {"messages": AIMessage(content=routed)}
        return {"messages": AIMessage(content="普通回复")}

    monkeypatch.setattr(
        "agents.roles.supervisor._execute_llm_query_safely", chatter_response
    )
    return make_agent_graph(
        _llm_config(),
        ":memory:",
        None,
        memory_store=store,
        memory_interpreter=interpreter,
    )


@pytest.mark.asyncio
async def test_load_memories_reads_fresh_database_state_on_every_request(
    tmp_path, monkeypatch
):
    """Breaks if graph construction snapshots memory instead of each invocation."""
    store = UserMemoryStore(tmp_path / "user-memory.db")
    app = _build_graph(monkeypatch, store, DeterministicInterpreter())

    first = await app.ainvoke(
        {"messages": [HumanMessage(content="你好")]},
        config={"configurable": {"thread_id": "thread-a", "user_id": "user-a"}},
    )
    assert "乳糖不耐受" not in first["memory_context"]

    store.remember(
        owner_key_for("user-a"),
        NewUserMemory(
            memory_type=MemoryType.HEALTH_CONSTRAINT,
            canonical_key="乳糖不耐受",
            display_name="乳糖不耐受",
            content="我乳糖不耐受",
        ),
    )
    second = await app.ainvoke(
        {
            "messages": [HumanMessage(content="再说一次你好")],
            "memory_context": "STALE SNAPSHOT",
        },
        config={"configurable": {"thread_id": "thread-a", "user_id": "user-a"}},
    )

    assert "[Durable User Memories — database-backed]" in second["memory_context"]
    assert "我乳糖不耐受" in second["memory_context"]
    assert "STALE SNAPSHOT" not in second["memory_context"]


@pytest.mark.asyncio
async def test_saved_memory_loads_for_same_user_in_a_new_thread(tmp_path, monkeypatch):
    """Breaks if durable memory is keyed by conversation thread."""
    store = UserMemoryStore(tmp_path / "user-memory.db")
    app = _build_graph(
        monkeypatch,
        store,
        DeterministicInterpreter(_remember_lactose_decision()),
    )

    saved = await app.ainvoke(
        {"messages": [HumanMessage(content="记住我乳糖不耐受")]},
        config={"configurable": {"thread_id": "thread-a", "user_id": "user-a"}},
    )
    loaded = await app.ainvoke(
        {"messages": [HumanMessage(content="你好")]},
        config={"configurable": {"thread_id": "thread-b", "user_id": "user-a"}},
    )

    assert "已记住" in saved["messages"][-1].content
    assert "我乳糖不耐受" in saved["memory_context"]
    assert "我乳糖不耐受" in loaded["memory_context"]


@pytest.mark.asyncio
async def test_memory_load_isolated_by_configured_user_id(tmp_path, monkeypatch):
    """Breaks if two users share one durable-memory owner key."""
    store = UserMemoryStore(tmp_path / "user-memory.db")
    store.remember(
        owner_key_for("user-a"),
        NewUserMemory(
            memory_type=MemoryType.HEALTH_CONSTRAINT,
            canonical_key="乳糖不耐受",
            display_name="乳糖不耐受",
            content="我乳糖不耐受",
        ),
    )
    app = _build_graph(monkeypatch, store, DeterministicInterpreter())

    user_a = await app.ainvoke(
        {"messages": [HumanMessage(content="你好")]},
        config={"configurable": {"thread_id": "thread-a", "user_id": "user-a"}},
    )
    user_b = await app.ainvoke(
        {"messages": [HumanMessage(content="你好")]},
        config={"configurable": {"thread_id": "thread-b", "user_id": "user-b"}},
    )

    assert "我乳糖不耐受" in user_a["memory_context"]
    assert "我乳糖不耐受" not in user_b["memory_context"]


@pytest.mark.asyncio
async def test_memory_load_falls_back_to_thread_id_for_legacy_callers(
    tmp_path, monkeypatch
):
    """Breaks if a legacy invocation without user_id loses its memories."""
    store = UserMemoryStore(tmp_path / "user-memory.db")
    store.remember(
        owner_key_for("legacy-thread"),
        NewUserMemory(
            memory_type=MemoryType.PROFILE,
            canonical_key="name",
            display_name="姓名",
            content="Ada",
        ),
    )
    app = _build_graph(monkeypatch, store, DeterministicInterpreter())

    result = await app.ainvoke(
        {"messages": [HumanMessage(content="你好")]},
        config={"configurable": {"thread_id": "legacy-thread"}},
    )

    assert "Ada" in result["memory_context"]


async def _route_with_llm_decision(monkeypatch, message: str, decision: str):
    monkeypatch.setattr(
        "agents.roles.supervisor.create_chat_model",
        lambda config: object(),
    )

    async def route_response(llm, messages):
        del llm, messages
        return {"messages": AIMessage(content=decision)}

    monkeypatch.setattr(
        "agents.roles.supervisor._execute_llm_query_safely", route_response
    )
    return await route_assistant_on_relevance(
        _llm_config(), [HumanMessage(content=message)]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    (
        "记住我乳糖不耐受",
        "我不吃香菜，记下来",
        "把 2-1-3 模板更新成新的内容",
        "忘掉乳糖不耐受",
    ),
)
async def test_explicit_memory_commands_prepend_memory_route(message, monkeypatch):
    """Breaks if an explicit durable mutation can be routed only to chatter."""
    routes = await _route_with_llm_decision(monkeypatch, message, "chatter")

    assert routes == ["memory_agent"]


@pytest.mark.asyncio
async def test_ordinary_training_message_does_not_enter_memory(monkeypatch):
    """Breaks if normal business records are misclassified as durable memory."""
    routes = await _route_with_llm_decision(
        monkeypatch, "今天练了深蹲", "training_agent"
    )

    assert routes == ["training_agent"]
    assert "memory_agent" not in routes


@pytest.mark.asyncio
async def test_pending_confirmation_routes_back_to_memory(tmp_path, monkeypatch):
    """Breaks if a clarification reply abandons its pending memory operation."""
    store = UserMemoryStore(tmp_path / "user-memory.db")
    decision = MemoryMutationDecision(
        intent="remember",
        memory_type=MemoryType.PROFILE,
        canonical_key="name",
        display_name="姓名",
        content="Ada",
    )
    pending = PendingMemoryAction(
        owner_key=owner_key_for("user-a"),
        operation="remember",
        decision=decision,
        question="请确认保存姓名。",
    )
    app = _build_graph(
        monkeypatch,
        store,
        DeterministicInterpreter(MemoryMutationDecision(intent="remember")),
    )

    result = await app.ainvoke(
        {
            "messages": [HumanMessage(content="确认")],
            "pending_memory_action": pending,
        },
        config={"configurable": {"thread_id": "thread-a", "user_id": "user-a"}},
    )

    assert result["pending_memory_action"] is None
    assert [
        memory.content for memory in store.list_memories(owner_key_for("user-a"))
    ] == ["Ada"]


@pytest.mark.asyncio
async def test_composite_memory_and_insights_routes_are_both_retained(monkeypatch):
    """Breaks if adding a memory route discards the model-selected analysis route."""
    routes = await _route_with_llm_decision(
        monkeypatch,
        "记住这个模板并分析今天的训练",
        "insights_agent",
    )

    assert routes == ["memory_agent", "insights_agent"]


@pytest.mark.asyncio
async def test_supervisor_and_chatter_prompts_receive_both_context_layers(
    tmp_path, monkeypatch
) -> None:
    """Breaks if orchestration or chatter answers without durable user context."""
    store = UserMemoryStore(tmp_path / "user-memory.db")
    store.remember(
        owner_key_for("user-a"),
        NewUserMemory(
            memory_type=MemoryType.HEALTH_CONSTRAINT,
            canonical_key="乳糖不耐受",
            display_name="乳糖不耐受",
            content="我乳糖不耐受",
        ),
    )
    app = _build_graph(monkeypatch, store, DeterministicInterpreter())
    captured_prompts: list[str] = []

    async def capture_prompts(llm, messages):
        del llm
        captured_prompts.append(str(messages[0].content))
        if "assigning user input" in str(messages[0].content):
            return {"messages": AIMessage(content="chatter")}
        return {"messages": AIMessage(content="普通回复")}

    monkeypatch.setattr(
        "agents.roles.supervisor._execute_llm_query_safely", capture_prompts
    )

    await app.ainvoke(
        {
            "messages": [HumanMessage(content="你好")],
            "summary": "刚刚讨论了睡眠安排。",
        },
        config={"configurable": {"thread_id": "thread-a", "user_id": "user-a"}},
    )

    assert len(captured_prompts) == 2
    for prompt in captured_prompts:
        assert "[Durable User Memories — database-backed]" in prompt
        assert "我乳糖不耐受" in prompt
        assert "[Short-term Conversation Summary]" in prompt
        assert "刚刚讨论了睡眠安排。" in prompt


class _FakeToolModel:
    def bind_tools(self, tools):
        del tools
        return self


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "factory_name", "factory_extra_args"),
    (
        ("agents.roles.training", "make_training_agent_graph", (":memory:",)),
        ("agents.roles.meal", "make_meal_subagent_graph", (":memory:", None)),
        ("agents.roles.insights", "make_insights_agent_graph", (":memory:",)),
    ),
)
async def test_role_prompt_receives_durable_memory_and_distinct_summary(
    module_name,
    factory_name,
    factory_extra_args,
    monkeypatch,
) -> None:
    """Breaks if any specialist omits either context layer from its system input."""
    module = importlib.import_module(module_name)
    captured_prompts: list[str] = []
    monkeypatch.setattr(module, "create_chat_model", lambda config: _FakeToolModel())
    if hasattr(module, "ApprovalResolver"):
        monkeypatch.setattr(module, "ApprovalResolver", lambda config: object())

    async def capture_prompt(llm, messages):
        del llm
        captured_prompts.append(str(messages[0].content))
        return {"messages": AIMessage(content="角色回复")}

    monkeypatch.setattr(module, "_execute_llm_query_safely", capture_prompt)
    factory = getattr(module, factory_name)
    app = factory(_llm_config(), *factory_extra_args)

    await app.ainvoke(
        {
            "messages": [HumanMessage(content="测试角色提示")],
            "memory_context": (
                "[Durable User Memories — database-backed]\n"
                "- [health_constraint] 乳糖不耐受: 我乳糖不耐受"
            ),
            "summary": "刚刚讨论了睡眠安排。",
        }
    )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "[Durable User Memories — database-backed]" in prompt
    assert "我乳糖不耐受" in prompt
    assert "[Short-term Conversation Summary]" in prompt
    assert "刚刚讨论了睡眠安排。" in prompt
