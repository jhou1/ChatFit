import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from agents.memory.models import (
    MemoryConflictError,
    MemoryType,
    MemoryUpdate,
    NewUserMemory,
    StaleMemoryError,
    UserMemory,
)
from agents.memory.store import UserMemoryStore, normalize_memory_key, owner_key_for


def _training_template(
    *,
    canonical_key: str = "213",
    content: str = "10 kettlebell swings, 20 push-ups, and 30 squats",
    aliases: tuple[str, ...] = ("213", "2-1-3", "壶铃213"),
) -> NewUserMemory:
    return NewUserMemory(
        memory_type=MemoryType.TRAINING_TEMPLATE,
        canonical_key=canonical_key,
        display_name="2-1-3",
        content=content,
        aliases=aliases,
    )


def test_initializes_only_user_memory_tables(tmp_path):
    db_path = tmp_path / "user-memory.db"
    UserMemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == {"user_memories", "user_memory_aliases"}


def test_schema_enforces_memory_and_alias_ownership_constraints(tmp_path):
    db_path = tmp_path / "user-memory.db"
    UserMemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        memory = (
            "memory-a",
            "owner-a",
            "training_template",
            "213",
            "2-1-3",
            "content",
            "2026-08-03T00:00:00+00:00",
            "2026-08-03T00:00:00+00:00",
        )
        connection.execute(
            """
            INSERT INTO user_memories (
                id, owner_key, memory_type, canonical_key, display_name, content,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            memory,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO user_memories (
                    id, owner_key, memory_type, canonical_key, display_name, content,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("memory-b", *memory[1:]),
            )
        connection.execute(
            """
            INSERT INTO user_memory_aliases (
                owner_key, normalized_alias, display_alias, memory_id
            ) VALUES (?, ?, ?, ?)
            """,
            ("owner-a", "213", "213", "memory-a"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO user_memory_aliases (
                    owner_key, normalized_alias, display_alias, memory_id
                ) VALUES (?, ?, ?, ?)
                """,
                ("owner-a", "213", "two-one-three", "memory-a"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO user_memory_aliases (
                    owner_key, normalized_alias, display_alias, memory_id
                ) VALUES (?, ?, ?, ?)
                """,
                ("owner-a", "orphan", "orphan", "missing-memory"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO user_memory_aliases (
                    owner_key, normalized_alias, display_alias, memory_id
                ) VALUES (?, ?, ?, ?)
                """,
                ("owner-b", "foreign", "foreign", "memory-a"),
            )


def test_owner_and_alias_normalization_are_stable():
    owner = owner_key_for("telegram-123")
    assert owner == owner_key_for("telegram-123")
    assert owner == hashlib.sha256(b"chatfit-user-memory:telegram-123").hexdigest()
    assert len(owner) == 64
    assert "telegram-123" not in owner
    assert normalize_memory_key(" ２‐１‐３ ") == "2-1-3"
    assert normalize_memory_key("  Plan  ∶  Morning  ") == "plan:morning"
    assert MemoryType.TRAINING_TEMPLATE.value == "training_template"


def test_creates_parent_directory_and_rejects_directory_target(tmp_path):
    db_path = tmp_path / "nested" / "user-memory.db"
    UserMemoryStore(db_path)
    assert db_path.is_file()

    directory_target = tmp_path / "memory-directory"
    directory_target.mkdir()
    with pytest.raises(IsADirectoryError):
        UserMemoryStore(directory_target)


def test_user_memory_models_are_frozen():
    memory = UserMemory(
        id="memory-a",
        owner_key="owner-a",
        memory_type=MemoryType.PROFILE,
        canonical_key="name",
        display_name="Name",
        content="Ada",
        version=1,
        created_at="2026-08-03T00:00:00+00:00",
        updated_at="2026-08-03T00:00:00+00:00",
    )

    with pytest.raises(ValidationError):
        memory.content = "Grace"


def test_remember_is_idempotent_and_resolves_each_alias(tmp_path):
    db_path = tmp_path / "user-memory.db"
    store = UserMemoryStore(db_path)
    owner = owner_key_for("telegram-123")
    aliases = ("213", "2-1-3", "壶铃213")

    first = store.remember(owner, _training_template(aliases=aliases))
    second = store.remember(owner, _training_template(aliases=aliases))

    with sqlite3.connect(db_path) as connection:
        memory_row_count = connection.execute(
            "SELECT COUNT(*) FROM user_memories"
        ).fetchone()[0]
    assert first.status == "created"
    assert second.status == "unchanged"
    assert first.memory.id == second.memory.id
    assert memory_row_count == 1
    assert {store.resolve(owner, alias)[0].id for alias in aliases} == {first.memory.id}


def test_remember_conflicting_content_preserves_existing_memory(tmp_path):
    store = UserMemoryStore(tmp_path / "user-memory.db")
    owner = owner_key_for("telegram-123")
    original = store.remember(owner, _training_template()).memory

    with pytest.raises(MemoryConflictError):
        store.remember(owner, _training_template(content="a different workout"))

    stored = store.resolve(owner, "213")[0]
    assert stored.content == original.content
    assert stored.version == original.version


def test_alias_and_crud_operations_are_isolated_by_owner(tmp_path):
    store = UserMemoryStore(tmp_path / "user-memory.db")
    first_owner = owner_key_for("telegram-123")
    second_owner = owner_key_for("telegram-456")
    original = store.remember(first_owner, _training_template()).memory

    assert store.list_memories(second_owner) == []
    assert store.resolve(second_owner, "壶铃213") == []
    with pytest.raises(MemoryConflictError):
        store.update(
            second_owner,
            original.id,
            MemoryUpdate(
                display_name="Other owner's edit",
                content="must not be stored",
                expected_version=original.version,
            ),
        )
    assert store.forget(second_owner, original.id) is False
    assert store.list_memories(first_owner) == [original]


def test_update_keeps_id_increments_version_and_replaces_aliases(tmp_path):
    db_path = tmp_path / "user-memory.db"
    store = UserMemoryStore(db_path)
    owner = owner_key_for("telegram-123")
    original = store.remember(owner, _training_template()).memory
    resolved = store.resolve(owner, "壶铃213")[0]
    replacement = "15 kettlebell swings, 25 push-ups, and 35 squats"

    updated = store.update(
        owner,
        resolved.id,
        MemoryUpdate(
            display_name="Updated 2-1-3",
            content=replacement,
            aliases=("213", "updated-213", "壶铃新213"),
            expected_version=resolved.version,
        ),
    )

    with sqlite3.connect(db_path) as connection:
        memory_row_count = connection.execute(
            "SELECT COUNT(*) FROM user_memories"
        ).fetchone()[0]
        stored_aliases = {
            row[0]
            for row in connection.execute(
                "SELECT normalized_alias FROM user_memory_aliases"
            )
        }
    assert updated.id == original.id
    assert updated.version == original.version + 1
    assert updated.content == replacement
    assert memory_row_count == 1
    assert stored_aliases == {"213", "updated-213", "壶铃新213"}
    assert store.resolve(owner, "壶铃213") == []
    assert store.resolve(owner, "updated-213") == [updated]


def test_stale_update_preserves_current_memory(tmp_path):
    store = UserMemoryStore(tmp_path / "user-memory.db")
    owner = owner_key_for("telegram-123")
    original = store.remember(owner, _training_template()).memory
    current = store.update(
        owner,
        original.id,
        MemoryUpdate(
            display_name="Current plan",
            content="current content",
            aliases=("current-213",),
            expected_version=original.version,
        ),
    )

    with pytest.raises(StaleMemoryError):
        store.update(
            owner,
            original.id,
            MemoryUpdate(
                display_name="Stale plan",
                content="stale content",
                aliases=("stale-213",),
                expected_version=original.version,
            ),
        )

    assert store.list_memories(owner) == [current]
    assert store.resolve(owner, "current-213") == [current]
    assert store.resolve(owner, "stale-213") == []


def test_update_alias_conflict_rolls_back_memory_and_aliases(tmp_path):
    store = UserMemoryStore(tmp_path / "user-memory.db")
    owner = owner_key_for("telegram-123")
    original = store.remember(owner, _training_template()).memory
    other = store.remember(
        owner,
        NewUserMemory(
            memory_type=MemoryType.PROFILE,
            canonical_key="name",
            display_name="Name",
            content="Ada",
            aliases=("shared-alias",),
        ),
    ).memory

    with pytest.raises(MemoryConflictError):
        store.update(
            owner,
            original.id,
            MemoryUpdate(
                display_name="Conflicting plan",
                content="must be rolled back",
                aliases=("shared-alias",),
                expected_version=original.version,
            ),
        )

    assert store.list_memories(owner) == [original, other]
    assert store.resolve(owner, "壶铃213") == [original]
    assert store.resolve(owner, "shared-alias") == [other]


def test_update_preserves_user_facing_canonical_alias_spelling(tmp_path):
    db_path = tmp_path / "user-memory.db"
    store = UserMemoryStore(db_path)
    owner = owner_key_for("telegram-123")
    original = store.remember(
        owner,
        _training_template(
            canonical_key="２‐１‐３",
            aliases=("壶铃213",),
        ),
    ).memory

    updated = store.update(
        owner,
        original.id,
        MemoryUpdate(
            display_name="Updated 2-1-3",
            content="updated content",
            aliases=("updated-213",),
            expected_version=original.version,
        ),
    )

    with sqlite3.connect(db_path) as connection:
        canonical_display_alias = connection.execute(
            """
            SELECT display_alias
            FROM user_memory_aliases
            WHERE owner_key = ? AND normalized_alias = ?
            """,
            (owner, "2-1-3"),
        ).fetchone()[0]
    assert canonical_display_alias == "２‐１‐３"
    assert "２‐１‐３" in updated.aliases


def test_forget_physically_deletes_memory_and_cascades_aliases(tmp_path):
    db_path = tmp_path / "user-memory.db"
    store = UserMemoryStore(db_path)
    owner = owner_key_for("telegram-123")
    original = store.remember(owner, _training_template()).memory

    assert store.forget(owner, original.id) is True

    with sqlite3.connect(db_path) as connection:
        memory_row_count = connection.execute(
            "SELECT COUNT(*) FROM user_memories"
        ).fetchone()[0]
        alias_row_count = connection.execute(
            "SELECT COUNT(*) FROM user_memory_aliases"
        ).fetchone()[0]
    assert memory_row_count == 0
    assert alias_row_count == 0
    assert store.list_memories(owner) == []
    assert store.resolve(owner, "213") == []
    assert store.forget(owner, original.id) is False


def test_concurrent_remember_creates_one_canonical_memory(tmp_path):
    db_path = tmp_path / "user-memory.db"
    store = UserMemoryStore(db_path)
    owner = owner_key_for("telegram-123")
    barrier = Barrier(2)

    def remember_after_barrier():
        barrier.wait()
        return store.remember(owner, _training_template())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: remember_after_barrier(), range(2)))

    with sqlite3.connect(db_path) as connection:
        canonical_rows = connection.execute(
            """
            SELECT id
            FROM user_memories
            WHERE owner_key = ? AND memory_type = ? AND canonical_key = ?
            """,
            (owner, MemoryType.TRAINING_TEMPLATE.value, "213"),
        ).fetchall()
    assert sorted(result.status for result in results) == ["created", "unchanged"]
    assert len(canonical_rows) == 1
    assert {result.memory.id for result in results} == {canonical_rows[0][0]}
