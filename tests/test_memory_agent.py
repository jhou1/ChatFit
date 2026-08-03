import sqlite3

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from agents.llm_factory import LLMConfig
from agents.memory.agent import LLMMemoryInterpreter, MemoryAgent
from agents.memory.models import (
    MemoryMutationDecision,
    MemoryType,
    MemoryUpdate,
    NewUserMemory,
    PendingMemoryAction,
    UserMemory,
)
from agents.memory.store import UserMemoryStore, owner_key_for


class DeterministicInterpreter:
    def __init__(self, *decisions: MemoryMutationDecision) -> None:
        self._decisions = iter(decisions)

    async def interpret(self, *, user_message, memories, pending):
        del user_message, memories, pending
        return next(self._decisions)


def _training_decision(
    *,
    key: str = "213",
    display_name: str = "壶铃213",
    content: str = "原始模板内容",
    aliases: tuple[str, ...] = ("壶铃213", "2-1-3"),
) -> MemoryMutationDecision:
    return MemoryMutationDecision(
        intent="remember",
        memory_type=MemoryType.TRAINING_TEMPLATE,
        canonical_key=key,
        display_name=display_name,
        content=content,
        aliases=aliases,
    )


@pytest.mark.asyncio
async def test_ordinary_message_cannot_authorize_interpreter_remember(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    agent = MemoryAgent(
        store=UserMemoryStore(memory_db),
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="remember",
                memory_type=MemoryType.PROFILE,
                canonical_key="name",
                display_name="姓名",
                content="模型猜测的名字",
            )
        ),
    )

    result = await agent.handle(
        user_id="user-a", user_message="今天天气不错", pending=None
    )

    with sqlite3.connect(memory_db) as connection:
        count = connection.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0]
    assert count == 0
    assert result.pending is None
    assert "已记住" not in result.response


@pytest.mark.asyncio
async def test_explicit_command_cannot_authorize_wrong_interpreter_intent(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    owner = owner_key_for("user-a")
    original = store.remember(
        owner,
        NewUserMemory(
            memory_type=MemoryType.TRAINING_TEMPLATE,
            canonical_key="213",
            display_name="壶铃213",
            content="原模板内容",
            aliases=("壶铃213",),
        ),
    ).memory
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="remember",
                memory_type=MemoryType.PROFILE,
                canonical_key="malicious",
                display_name="恶意记忆",
                content="不应保存",
            )
        ),
    )

    result = await agent.handle(
        user_id="user-a", user_message="忘掉壶铃213", pending=None
    )

    assert store.list_memories(owner) == [original]
    assert result.pending is None
    assert all(word not in result.response for word in ("已记住", "已更新", "已忘掉"))


@pytest.mark.asyncio
async def test_business_record_delete_cannot_authorize_memory_forget(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    owner = owner_key_for("user-a")
    original = store.remember(
        owner,
        NewUserMemory(
            memory_type=MemoryType.TRAINING_TEMPLATE,
            canonical_key="213",
            display_name="壶铃213",
            content="原模板内容",
            aliases=("壶铃213",),
        ),
    ).memory
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(intent="forget", target_query="壶铃213")
        ),
    )

    result = await agent.handle(
        user_id="user-a", user_message="删除今天的训练记录", pending=None
    )

    assert store.list_memories(owner) == [original]
    assert result.pending is None
    assert "已忘掉" not in result.response


@pytest.mark.asyncio
async def test_none_interpreter_intent_fails_closed_without_mutation(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    agent = MemoryAgent(
        store=UserMemoryStore(memory_db),
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent=None,
                memory_type=MemoryType.PROFILE,
                canonical_key="name",
                display_name="姓名",
                content="不应保存",
            )
        ),
    )

    result = await agent.handle(
        user_id="user-a", user_message="记住我的名字", pending=None
    )

    with sqlite3.connect(memory_db) as connection:
        count = connection.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0]
    assert count == 0
    assert result.pending is None
    assert "已记住" not in result.response


@pytest.mark.asyncio
async def test_explicit_remember_commits_exact_chinese_payload(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    interpreter = DeterministicInterpreter(
        MemoryMutationDecision(
            intent="remember",
            memory_type=MemoryType.DIETARY_PREFERENCE,
            canonical_key="乳糖不耐受",
            display_name="乳糖不耐受",
            content="模型改写的不耐乳糖描述",
            aliases=("乳糖不耐",),
        )
    )
    agent = MemoryAgent(store=store, interpreter=interpreter)

    result = await agent.handle(
        user_id="user-a",
        user_message="记住我乳糖不耐受",
        pending=None,
    )

    with sqlite3.connect(memory_db) as connection:
        row = connection.execute(
            "SELECT memory_type, canonical_key, content FROM user_memories"
        ).fetchone()
    assert row == ("dietary_preference", "乳糖不耐受", "我乳糖不耐受")
    assert "已记住" in result.response
    assert result.pending is None


@pytest.mark.asyncio
async def test_suffix_remember_commits_exact_chinese_payload(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    agent = MemoryAgent(
        store=UserMemoryStore(memory_db),
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="remember",
                memory_type=MemoryType.DIETARY_PREFERENCE,
                canonical_key="不吃香菜",
                display_name="不吃香菜",
                content="模型改写的香菜偏好",
            )
        ),
    )

    await agent.handle(
        user_id="user-a",
        user_message="我不吃香菜，记下来",
        pending=None,
    )

    with sqlite3.connect(memory_db) as connection:
        content = connection.execute("SELECT content FROM user_memories").fetchone()[0]
    assert content == "我不吃香菜"


@pytest.mark.asyncio
async def test_empty_explicit_remember_payload_never_stores_model_content(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    agent = MemoryAgent(
        store=UserMemoryStore(memory_db),
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="remember",
                memory_type=MemoryType.OTHER,
                canonical_key="invented",
                display_name="模型猜测",
                content="模型擅自补出的内容",
                clarification_question="你希望我记住什么？",
            ),
            MemoryMutationDecision(intent="forget", content="模型改写"),
        ),
    )

    result = await agent.handle(user_id="user-a", user_message="记住", pending=None)

    with sqlite3.connect(memory_db) as connection:
        count = connection.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0]
    assert count == 0
    assert result.response == "你希望我记住什么？"
    assert result.pending is not None

    completed = await agent.handle(
        user_id="user-a", user_message="  Ada  ", pending=result.pending
    )

    with sqlite3.connect(memory_db) as connection:
        rows = connection.execute("SELECT content FROM user_memories").fetchall()
    assert rows == [("  Ada  ",)]
    assert completed.pending is None
    assert "已记住" in completed.response


@pytest.mark.asyncio
async def test_whitespace_only_remember_payload_requests_clarification(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    agent = MemoryAgent(
        store=UserMemoryStore(memory_db),
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="remember",
                memory_type=MemoryType.OTHER,
                canonical_key="invented",
                display_name="模型猜测",
                content="模型擅自补出的内容",
                clarification_question="你希望我记住什么？",
            ),
            MemoryMutationDecision(intent="forget", content="模型改写"),
        ),
    )

    result = await agent.handle(user_id="user-a", user_message="记住   ", pending=None)

    with sqlite3.connect(memory_db) as connection:
        count = connection.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0]
    assert count == 0
    assert result.response == "你希望我记住什么？"
    assert result.pending is not None

    completed = await agent.handle(
        user_id="user-a", user_message="  Ada  ", pending=result.pending
    )

    with sqlite3.connect(memory_db) as connection:
        rows = connection.execute("SELECT content FROM user_memories").fetchall()
    assert rows == [("  Ada  ",)]
    assert completed.pending is None
    assert "已记住" in completed.response


@pytest.mark.asyncio
async def test_pending_remember_uses_exact_user_reply_not_model_content(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="remember",
                memory_type=MemoryType.PROFILE,
                canonical_key="name",
                display_name="姓名",
                content="模型第一次猜测",
                clarification_question="你希望我记住什么？",
            ),
            MemoryMutationDecision(
                intent="forget",
                content="模型第二次改写",
            ),
        ),
    )
    pending_result = await agent.handle(
        user_id="user-a", user_message="记住", pending=None
    )
    assert pending_result.pending is not None

    result = await agent.handle(
        user_id="user-a", user_message="  Ada  ", pending=pending_result.pending
    )

    stored = store.list_memories(owner_key_for("user-a"))
    assert len(stored) == 1
    assert stored[0].content == "  Ada  "
    assert "已记住" in result.response


@pytest.mark.asyncio
async def test_update_resolves_alias_and_replaces_same_database_row(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            _training_decision(),
            MemoryMutationDecision(
                intent="update",
                target_query="壶铃213",
                display_name="壶铃213",
                content="模型改写的新模板内容",
                aliases=("壶铃213", "新版213"),
            ),
        ),
    )
    await agent.handle(user_id="user-a", user_message="记住原始模板内容", pending=None)
    with sqlite3.connect(memory_db) as connection:
        original_id = connection.execute("SELECT id FROM user_memories").fetchone()[0]

    result = await agent.handle(
        user_id="user-a",
        user_message="把壶铃213更新成新的模板内容",
        pending=None,
    )

    with sqlite3.connect(memory_db) as connection:
        rows = connection.execute(
            "SELECT id, version, content FROM user_memories"
        ).fetchall()
    assert rows == [(original_id, 2, "新的模板内容")]
    assert store.resolve(owner_key_for("user-a"), "壶铃213")[0].id == original_id
    assert "已更新" in result.response
    assert result.pending is None


@pytest.mark.asyncio
async def test_whitespace_only_update_preserves_row_and_requests_clarification(
    tmp_path,
):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    owner = owner_key_for("user-a")
    original = store.remember(
        owner,
        NewUserMemory(
            memory_type=MemoryType.TRAINING_TEMPLATE,
            canonical_key="213",
            display_name="壶铃213",
            content="原模板内容",
            aliases=("壶铃213",),
        ),
    ).memory
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="update",
                target_query="壶铃213",
                content="模型改写",
                clarification_question="请提供新的模板内容。",
            ),
            MemoryMutationDecision(intent="forget", target_query="壶铃213"),
        ),
    )

    result = await agent.handle(
        user_id="user-a", user_message="把壶铃213更新成   ", pending=None
    )

    assert store.list_memories(owner) == [original]
    assert result.pending is not None
    assert "已更新" not in result.response

    completed = await agent.handle(
        user_id="user-a", user_message="  exact  ", pending=result.pending
    )

    stored = store.list_memories(owner)
    assert len(stored) == 1
    assert stored[0].id == original.id
    assert stored[0].version == 2
    assert stored[0].content == "  exact  "
    assert completed.pending is None
    assert "已更新" in completed.response


@pytest.mark.asyncio
async def test_repeated_remember_is_idempotent_at_agent_boundary(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    decision = _training_decision(content="同一个模板")
    agent = MemoryAgent(
        store=UserMemoryStore(memory_db),
        interpreter=DeterministicInterpreter(decision, decision),
    )

    first = await agent.handle(
        user_id="user-a", user_message="记住同一个模板", pending=None
    )
    second = await agent.handle(
        user_id="user-a", user_message="记住同一个模板", pending=None
    )

    with sqlite3.connect(memory_db) as connection:
        rows = connection.execute(
            "SELECT id, version, content FROM user_memories"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][1:] == (1, "同一个模板")
    assert "已记住" in first.response
    assert "已记住" in second.response


@pytest.mark.asyncio
async def test_conflicting_remember_waits_for_update_confirmation(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            _training_decision(content="旧模板内容"),
            _training_decision(content="新模板内容"),
            MemoryMutationDecision(
                intent="update",
                target_query="壶铃213",
                content="模型改写的新模板内容",
            ),
        ),
    )
    await agent.handle(user_id="user-a", user_message="记住旧模板内容", pending=None)

    conflict = await agent.handle(
        user_id="user-a", user_message="记住新模板内容", pending=None
    )

    with sqlite3.connect(memory_db) as connection:
        before_confirmation = connection.execute(
            "SELECT version, content FROM user_memories"
        ).fetchall()
    assert before_confirmation == [(1, "旧模板内容")]
    assert conflict.pending is not None
    assert len(conflict.pending.candidate_ids) == 1
    assert "更新" in conflict.response

    confirmed = await agent.handle(
        user_id="user-a", user_message="确认更新", pending=conflict.pending
    )

    with sqlite3.connect(memory_db) as connection:
        after_confirmation = connection.execute(
            "SELECT version, content FROM user_memories"
        ).fetchall()
    assert after_confirmation == [(2, "新模板内容")]
    assert "已更新" in confirmed.response


@pytest.mark.asyncio
async def test_forget_physically_deletes_exact_memory_and_aliases(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="remember",
                memory_type=MemoryType.DIETARY_PREFERENCE,
                canonical_key="乳糖不耐受",
                display_name="乳糖不耐受",
                content="我乳糖不耐受",
                aliases=("乳糖不耐",),
            ),
            MemoryMutationDecision(intent="forget", target_query="乳糖不耐受"),
        ),
    )
    await agent.handle(user_id="user-a", user_message="记住我乳糖不耐受", pending=None)

    result = await agent.handle(
        user_id="user-a", user_message="忘掉我乳糖不耐受", pending=None
    )

    with sqlite3.connect(memory_db) as connection:
        memory_count = connection.execute(
            "SELECT COUNT(*) FROM user_memories"
        ).fetchone()[0]
        alias_count = connection.execute(
            "SELECT COUNT(*) FROM user_memory_aliases"
        ).fetchone()[0]
    assert (memory_count, alias_count) == (0, 0)
    assert "已忘掉" in result.response
    assert result.pending is None


@pytest.mark.asyncio
async def test_ambiguous_forget_waits_then_deletes_only_confirmed_target(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            _training_decision(),
            _training_decision(
                key="morning",
                display_name="晨练模板",
                content="晨练内容",
                aliases=("晨练",),
            ),
            MemoryMutationDecision(
                intent="clarify",
                memory_type=MemoryType.TRAINING_TEMPLATE,
                target_query="那个训练模板",
                clarification_question="你是指壶铃213还是晨练模板？",
            ),
            MemoryMutationDecision(
                intent="remember",
                target_query="壶铃213",
                memory_type=MemoryType.PROFILE,
                canonical_key="malicious",
                display_name="恶意记忆",
                content="不应保存",
            ),
        ),
    )
    await agent.handle(user_id="user-a", user_message="记住原始模板内容", pending=None)
    await agent.handle(user_id="user-a", user_message="记住晨练内容", pending=None)

    before = store.list_memories(owner_key_for("user-a"))
    clarification = await agent.handle(
        user_id="user-a", user_message="忘掉那个训练模板", pending=None
    )
    after_clarification = store.list_memories(owner_key_for("user-a"))

    assert "壶铃213" in clarification.response
    assert "晨练模板" in clarification.response
    assert clarification.pending is not None
    assert len(clarification.pending.candidate_ids) == 2
    assert after_clarification == before

    forgotten = await agent.handle(
        user_id="user-a",
        user_message="壶铃213",
        pending=clarification.pending,
    )

    remaining = store.list_memories(owner_key_for("user-a"))
    assert [memory.display_name for memory in remaining] == ["晨练模板"]
    assert "已忘掉" in forgotten.response
    assert forgotten.pending is None


@pytest.mark.asyncio
async def test_pending_action_is_bound_to_original_owner(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="remember",
                memory_type=MemoryType.PROFILE,
                canonical_key="name",
                display_name="姓名",
                content="模型猜测",
                clarification_question="你希望我记住什么？",
            ),
            MemoryMutationDecision(
                intent="remember",
                memory_type=MemoryType.PROFILE,
                canonical_key="name",
                display_name="姓名",
                content="Ada",
            ),
        ),
    )
    pending_result = await agent.handle(
        user_id="user-a", user_message="记住", pending=None
    )
    assert pending_result.pending is not None

    result = await agent.handle(
        user_id="user-b", user_message="Ada", pending=pending_result.pending
    )

    assert store.list_memories(owner_key_for("user-a")) == []
    assert store.list_memories(owner_key_for("user-b")) == []
    assert result.pending is None
    assert "已记住" not in result.response


@pytest.mark.asyncio
async def test_pending_update_cannot_be_changed_to_forget(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    owner = owner_key_for("user-a")
    original = store.remember(
        owner,
        NewUserMemory(
            memory_type=MemoryType.TRAINING_TEMPLATE,
            canonical_key="213",
            display_name="壶铃213",
            content="原模板内容",
            aliases=("壶铃213",),
        ),
    ).memory
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="clarify",
                memory_type=MemoryType.TRAINING_TEMPLATE,
                target_query="壶铃213",
                clarification_question="请提供新的模板内容。",
            ),
            MemoryMutationDecision(intent="forget", target_query="壶铃213"),
        ),
    )
    pending_result = await agent.handle(
        user_id="user-a", user_message="更新这个记忆", pending=None
    )
    assert pending_result.pending is not None

    result = await agent.handle(
        user_id="user-a",
        user_message="新的模板内容",
        pending=pending_result.pending,
    )

    stored = store.list_memories(owner)
    assert len(stored) == 1
    assert stored[0].id == original.id
    assert stored[0].version == 2
    assert stored[0].content == "新的模板内容"
    assert "已更新" in result.response


@pytest.mark.asyncio
async def test_pending_update_preserves_exact_explicit_replacement(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    owner = owner_key_for("user-a")
    first = store.remember(
        owner,
        NewUserMemory(
            memory_type=MemoryType.TRAINING_TEMPLATE,
            canonical_key="213",
            display_name="壶铃213",
            content="模板一",
            aliases=("壶铃213",),
        ),
    ).memory
    second = store.remember(
        owner,
        NewUserMemory(
            memory_type=MemoryType.TRAINING_TEMPLATE,
            canonical_key="morning",
            display_name="晨练",
            content="模板二",
            aliases=("晨练",),
        ),
    ).memory
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="clarify",
                memory_type=MemoryType.TRAINING_TEMPLATE,
                target_query="那个训练模板",
                content="模型第一次改写",
                clarification_question="请确认训练模板。",
            ),
            MemoryMutationDecision(
                intent="forget",
                target_query="壶铃213",
                content="模型第二次改写",
            ),
        ),
    )
    pending_result = await agent.handle(
        user_id="user-a",
        user_message="把那个训练模板更新成  精确内容  ",
        pending=None,
    )
    assert pending_result.pending is not None

    result = await agent.handle(
        user_id="user-a", user_message="壶铃213", pending=pending_result.pending
    )

    stored = store.list_memories(owner)
    updated = next(memory for memory in stored if memory.id == first.id)
    unchanged = next(memory for memory in stored if memory.id == second.id)
    assert updated.content == "  精确内容  "
    assert updated.version == 2
    assert unchanged == second
    assert "已更新" in result.response


@pytest.mark.asyncio
async def test_pending_confirmation_cannot_escape_captured_candidates(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    owner = owner_key_for("user-a")
    memories = [
        store.remember(
            owner,
            NewUserMemory(
                memory_type=memory_type,
                canonical_key=key,
                display_name=name,
                content=content,
                aliases=(name,),
            ),
        ).memory
        for memory_type, key, name, content in (
            (MemoryType.TRAINING_TEMPLATE, "213", "壶铃213", "模板一"),
            (MemoryType.TRAINING_TEMPLATE, "morning", "晨练", "模板二"),
            (MemoryType.PROFILE, "name", "姓名", "Ada"),
        )
    ]
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="clarify",
                memory_type=MemoryType.TRAINING_TEMPLATE,
                target_query="那个训练模板",
                clarification_question="请确认训练模板。",
            ),
            MemoryMutationDecision(intent="forget", target_query="姓名"),
            MemoryMutationDecision(intent="forget", target_query="姓名"),
        ),
    )
    pending_result = await agent.handle(
        user_id="user-a", user_message="忘掉那个训练模板", pending=None
    )
    assert pending_result.pending is not None

    first_rejection = await agent.handle(
        user_id="user-a", user_message="姓名", pending=pending_result.pending
    )
    assert first_rejection.pending is not None
    second_rejection = await agent.handle(
        user_id="user-a", user_message="姓名", pending=first_rejection.pending
    )

    assert store.list_memories(owner) == memories
    assert second_rejection.pending is not None


def test_pending_action_rejects_misaligned_candidate_versions(tmp_path):
    store = UserMemoryStore(tmp_path / "user-memory.db")
    owner = owner_key_for("user-a")
    original = store.remember(
        owner,
        NewUserMemory(
            memory_type=MemoryType.PROFILE,
            canonical_key="name",
            display_name="姓名",
            content="Ada",
        ),
    ).memory

    with pytest.raises(ValidationError):
        PendingMemoryAction(
            owner_key=owner,
            operation="forget",
            decision=MemoryMutationDecision(
                intent="forget",
                target_query="姓名",
            ),
            candidate_ids=(original.id,),
            candidate_versions=(),
            question="请确认。",
        )

    assert store.list_memories(owner) == [original]


@pytest.mark.asyncio
async def test_stale_pending_confirmation_preserves_newer_memory(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            _training_decision(),
            MemoryMutationDecision(
                intent="forget",
                memory_type=MemoryType.TRAINING_TEMPLATE,
                target_query="那个模板",
                clarification_question="请确认要忘掉哪个训练模板。",
            ),
            MemoryMutationDecision(intent="forget", target_query="壶铃213"),
        ),
    )
    await agent.handle(user_id="user-a", user_message="记住原始模板内容", pending=None)
    clarification = await agent.handle(
        user_id="user-a", user_message="忘掉那个模板", pending=None
    )
    assert clarification.pending is not None
    original = store.resolve(owner_key_for("user-a"), "壶铃213")[0]
    current = store.update(
        owner_key_for("user-a"),
        original.id,
        MemoryUpdate(
            display_name=original.display_name,
            content="外部更新后的内容",
            aliases=original.aliases,
            expected_version=original.version,
        ),
    )

    result = await agent.handle(
        user_id="user-a", user_message="壶铃213", pending=clarification.pending
    )

    assert "更新" in result.response
    assert "确认" in result.response
    assert store.list_memories(owner_key_for("user-a")) == [current]


@pytest.mark.asyncio
async def test_forget_race_uses_atomic_expected_version(tmp_path, monkeypatch):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    owner = owner_key_for("user-a")
    original = store.remember(
        owner,
        NewUserMemory(
            memory_type=MemoryType.DIETARY_PREFERENCE,
            canonical_key="乳糖不耐受",
            display_name="乳糖不耐受",
            content="我乳糖不耐受",
            aliases=("乳糖不耐",),
        ),
    ).memory
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(intent="forget", target_query="乳糖不耐受")
        ),
    )
    real_forget = store.forget

    def race_forget(owner_key, memory_id, *, expected_version):
        current = store.resolve(owner_key, "乳糖不耐受")[0]
        store.update(
            owner_key,
            current.id,
            MemoryUpdate(
                display_name=current.display_name,
                content="并发更新后的内容",
                aliases=current.aliases,
                expected_version=current.version,
            ),
        )
        return real_forget(owner_key, memory_id, expected_version=expected_version)

    monkeypatch.setattr(store, "forget", race_forget)

    result = await agent.handle(
        user_id="user-a", user_message="忘掉乳糖不耐受", pending=None
    )

    stored = store.list_memories(owner)
    assert len(stored) == 1
    assert stored[0].id == original.id
    assert stored[0].version == 2
    assert stored[0].content == "并发更新后的内容"
    assert "更新" in result.response
    assert "确认" in result.response
    assert "已忘掉" not in result.response


@pytest.mark.asyncio
async def test_update_race_returns_stale_review_response(tmp_path, monkeypatch):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    owner = owner_key_for("user-a")
    original = store.remember(
        owner,
        NewUserMemory(
            memory_type=MemoryType.TRAINING_TEMPLATE,
            canonical_key="213",
            display_name="壶铃213",
            content="原模板内容",
            aliases=("壶铃213",),
        ),
    ).memory
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(
                intent="update",
                target_query="壶铃213",
                content="模型改写",
            )
        ),
    )
    real_update = store.update

    def race_update(owner_key, memory_id, change):
        current = store.resolve(owner_key, "壶铃213")[0]
        real_update(
            owner_key,
            current.id,
            MemoryUpdate(
                display_name=current.display_name,
                content="并发更新后的内容",
                aliases=current.aliases,
                expected_version=current.version,
            ),
        )
        return real_update(owner_key, memory_id, change)

    monkeypatch.setattr(store, "update", race_update)

    result = await agent.handle(
        user_id="user-a",
        user_message="把壶铃213更新成用户精确内容",
        pending=None,
    )

    stored = store.list_memories(owner)
    assert len(stored) == 1
    assert stored[0].id == original.id
    assert stored[0].version == 2
    assert stored[0].content == "并发更新后的内容"
    assert "更新" in result.response
    assert "确认" in result.response
    assert "已更新" not in result.response


@pytest.mark.asyncio
async def test_missing_target_requests_clarification_without_mutation(tmp_path):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(
            MemoryMutationDecision(intent="forget", target_query="不存在的记忆")
        ),
    )

    result = await agent.handle(
        user_id="user-a", user_message="忘掉不存在的记忆", pending=None
    )

    assert result.pending is not None
    assert result.pending.candidate_ids == ()
    with sqlite3.connect(memory_db) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0] == 0
        )


@pytest.mark.asyncio
async def test_repository_failure_never_returns_mutation_success(tmp_path, monkeypatch):
    memory_db = tmp_path / "user-memory.db"
    store = UserMemoryStore(memory_db)
    agent = MemoryAgent(
        store=store,
        interpreter=DeterministicInterpreter(_training_decision()),
    )

    def fail_remember(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "remember", fail_remember)
    result = await agent.handle(
        user_id="user-a", user_message="记住原始模板内容", pending=None
    )

    assert all(word not in result.response for word in ("已记住", "已更新", "已忘掉"))
    with sqlite3.connect(memory_db) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0] == 0
        )


@pytest.mark.asyncio
async def test_llm_interpreter_uses_structured_output_and_exact_context(monkeypatch):
    captured = {}

    class FakeStructuredRunnable:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return {
                "intent": "update",
                "target_query": "壶铃213",
                "display_name": "壶铃213",
                "content": "新的模板内容",
                "aliases": ["壶铃213", "2-1-3"],
            }

    class FakeChatModel:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return FakeStructuredRunnable()

    config = LLMConfig(provider="local", model_name="fake-memory-model")
    monkeypatch.setattr(
        "agents.memory.agent.create_chat_model", lambda received: FakeChatModel()
    )
    interpreter = LLMMemoryInterpreter(config)
    memory = UserMemory(
        id="memory-213",
        owner_key="owner-a",
        memory_type=MemoryType.TRAINING_TEMPLATE,
        canonical_key="213",
        display_name="壶铃213",
        content="原始模板内容",
        aliases=("213", "壶铃213"),
        version=1,
        created_at="2026-08-03T00:00:00+00:00",
        updated_at="2026-08-03T00:00:00+00:00",
    )

    decision = await interpreter.interpret(
        user_message="把壶铃213更新成新的模板内容",
        memories=[memory],
        pending=None,
    )

    assert captured["schema"] is MemoryMutationDecision
    assert captured["messages"] == [
        SystemMessage(
            content=(
                "You classify explicit user-memory mutation commands. Return one "
                "intent: remember, update, forget, or clarify. Preserve the user's "
                "explicit memory content verbatim; never paraphrase it. Use only the "
                "supplied current memory names and aliases to identify targets. If "
                "the command is incomplete or could match zero or multiple memories, "
                "choose clarify rather than guessing."
            )
        ),
        HumanMessage(
            content=(
                'User message:\n把壶铃213更新成新的模板内容\n\nCurrent memories:\n[{"id":'
                '"memory-213","memory_type":"training_template","canonical_key":"213",'
                '"display_name":"壶铃213","aliases":["213","壶铃213"],"version":1}]'
                "\n\nPending action:\nnull"
            )
        ),
    ]
    assert decision == MemoryMutationDecision(
        intent="update",
        target_query="壶铃213",
        display_name="壶铃213",
        content="新的模板内容",
        aliases=("壶铃213", "2-1-3"),
    )


def test_memory_package_exports_agent_service_contract():
    from agents import memory

    assert memory.MemoryAgent is MemoryAgent
    assert memory.LLMMemoryInterpreter is LLMMemoryInterpreter
    assert memory.MemoryMutationDecision is MemoryMutationDecision
