import hashlib
import sqlite3

import pytest
from pydantic import ValidationError

from agents.memory.models import MemoryType, UserMemory
from agents.memory.store import UserMemoryStore, normalize_memory_key, owner_key_for


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
