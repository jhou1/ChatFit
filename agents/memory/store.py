"""SQLite schema initialization for isolated durable user memory."""

import hashlib
import re
import sqlite3
import unicodedata
from pathlib import Path

_HYPHEN_VARIANTS = "‐‑‒–—―−﹘﹣－"
_COLON_VARIANTS = "︓﹕：∶꞉"
_SEPARATOR_TRANSLATION = str.maketrans(
    {
        **{character: "-" for character in _HYPHEN_VARIANTS},
        **{character: ":" for character in _COLON_VARIANTS},
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_memories (
    id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK (
        memory_type IN (
            'training_template',
            'training_preference',
            'dietary_preference',
            'health_constraint',
            'profile',
            'other'
        )
    ),
    canonical_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (owner_key, memory_type, canonical_key),
    UNIQUE (id, owner_key)
);

CREATE TABLE IF NOT EXISTS user_memory_aliases (
    owner_key TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    display_alias TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    PRIMARY KEY (owner_key, normalized_alias),
    FOREIGN KEY (memory_id, owner_key)
        REFERENCES user_memories (id, owner_key) ON DELETE CASCADE
);
"""


def owner_key_for(user_id: str) -> str:
    """Return a stable, non-reversible owner identifier for a user ID."""
    return hashlib.sha256(f"chatfit-user-memory:{user_id}".encode()).hexdigest()


def normalize_memory_key(value: str) -> str:
    """Normalize a canonical key or alias for durable matching."""
    normalized = unicodedata.normalize("NFKC", value).translate(_SEPARATOR_TRANSLATION)
    normalized = " ".join(normalized.lower().split())
    return re.sub(r"\s*([:-])\s*", r"\1", normalized)


class UserMemoryStore:
    """Initialize the database reserved for durable user memories."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.exists() and self.db_path.is_dir():
            raise IsADirectoryError(
                f"User-memory database path is a directory: {self.db_path}"
            )

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(_SCHEMA)
