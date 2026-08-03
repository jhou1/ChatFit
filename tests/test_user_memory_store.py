import sqlite3

from agents.memory.models import MemoryType
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
    assert {"user_memories", "user_memory_aliases"} <= tables
    assert "training_sessions" not in tables


def test_owner_and_alias_normalization_are_stable():
    owner = owner_key_for("telegram-123")
    assert owner == owner_key_for("telegram-123")
    assert len(owner) == 64
    assert "telegram-123" not in owner
    assert normalize_memory_key(" ２‐１‐３ ") == "2-1-3"
    assert MemoryType.TRAINING_TEMPLATE.value == "training_template"
