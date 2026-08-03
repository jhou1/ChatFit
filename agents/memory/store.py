"""SQLite schema initialization for isolated durable user memory."""

import hashlib
import re
import sqlite3
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.memory.models import (
    MemoryConflictError,
    MemoryUpdate,
    NewUserMemory,
    RememberResult,
    StaleMemoryError,
    UserMemory,
)

_HYPHEN_VARIANTS = "‐‑‒–—―−﹘﹣－"
_COLON_VARIANTS = "︓﹕：∶꞉"
_SEPARATOR_TRANSLATION = str.maketrans(
    {
        **{character: "-" for character in _HYPHEN_VARIANTS},
        **{character: ":" for character in _COLON_VARIANTS},
    }
)
_BUSY_TIMEOUT_MILLISECONDS = 2_000

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
        with self._connection() as connection:
            connection.executescript(_SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(
            sqlite3.connect(
                self.db_path,
                timeout=_BUSY_TIMEOUT_MILLISECONDS / 1_000,
            )
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MILLISECONDS}")
            with connection:
                yield connection

    @staticmethod
    def _alias_values(
        canonical_key: str, aliases: tuple[str, ...]
    ) -> list[tuple[str, str]]:
        values: dict[str, str] = {}
        for display_alias in (canonical_key, *aliases):
            normalized_alias = normalize_memory_key(display_alias)
            values.setdefault(normalized_alias, display_alias)
        return list(values.items())

    @staticmethod
    def _row_to_memory(connection: sqlite3.Connection, row: sqlite3.Row) -> UserMemory:
        aliases = tuple(
            alias["display_alias"]
            for alias in connection.execute(
                """
                SELECT display_alias
                FROM user_memory_aliases
                WHERE owner_key = ? AND memory_id = ?
                ORDER BY normalized_alias
                """,
                (row["owner_key"], row["id"]),
            )
        )
        values: dict[str, Any] = dict(row)
        values["aliases"] = aliases
        return UserMemory.model_validate(values)

    def _find_canonical(
        self,
        connection: sqlite3.Connection,
        owner_key: str,
        memory: NewUserMemory,
    ) -> UserMemory | None:
        row = connection.execute(
            """
            SELECT *
            FROM user_memories
            WHERE owner_key = ? AND memory_type = ? AND canonical_key = ?
            """,
            (
                owner_key,
                memory.memory_type.value,
                normalize_memory_key(memory.canonical_key),
            ),
        ).fetchone()
        return self._row_to_memory(connection, row) if row is not None else None

    def _find_by_id(
        self, connection: sqlite3.Connection, owner_key: str, memory_id: str
    ) -> UserMemory | None:
        row = connection.execute(
            """
            SELECT *
            FROM user_memories
            WHERE owner_key = ? AND id = ?
            """,
            (owner_key, memory_id),
        ).fetchone()
        return self._row_to_memory(connection, row) if row is not None else None

    def list_memories(self, owner_key: str) -> list[UserMemory]:
        """Return every durable memory owned by ``owner_key``."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM user_memories
                WHERE owner_key = ?
                ORDER BY created_at, id
                """,
                (owner_key,),
            ).fetchall()
            return [self._row_to_memory(connection, row) for row in rows]

    def resolve(self, owner_key: str, query: str) -> list[UserMemory]:
        """Resolve one normalized canonical key or alias for an owner."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT memories.*
                FROM user_memory_aliases AS aliases
                JOIN user_memories AS memories
                  ON memories.id = aliases.memory_id
                 AND memories.owner_key = aliases.owner_key
                WHERE aliases.owner_key = ? AND aliases.normalized_alias = ?
                ORDER BY memories.created_at, memories.id
                """,
                (owner_key, normalize_memory_key(query)),
            ).fetchall()
            return [self._row_to_memory(connection, row) for row in rows]

    def remember(self, owner_key: str, memory: NewUserMemory) -> RememberResult:
        """Create one canonical memory or return its identical existing row."""
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = self._find_canonical(connection, owner_key, memory)
                if existing is not None:
                    if existing.content != memory.content:
                        raise MemoryConflictError(
                            "A memory with this canonical key already has different content"
                        )
                    connection.commit()
                    return RememberResult(status="unchanged", memory=existing)

                memory_id = uuid.uuid4().hex
                timestamp = datetime.now(timezone.utc).isoformat()
                canonical_key = normalize_memory_key(memory.canonical_key)
                connection.execute(
                    """
                    INSERT INTO user_memories (
                        id, owner_key, memory_type, canonical_key, display_name,
                        content, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        memory_id,
                        owner_key,
                        memory.memory_type.value,
                        canonical_key,
                        memory.display_name,
                        memory.content,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO user_memory_aliases (
                        owner_key, normalized_alias, display_alias, memory_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        (owner_key, normalized_alias, display_alias, memory_id)
                        for normalized_alias, display_alias in self._alias_values(
                            memory.canonical_key, memory.aliases
                        )
                    ),
                )
                created = self._find_canonical(connection, owner_key, memory)
                if created is None:
                    raise RuntimeError("Inserted user memory could not be re-read")
                connection.commit()
                return RememberResult(status="created", memory=created)
            except sqlite3.IntegrityError:
                connection.rollback()
                existing = self._find_canonical(connection, owner_key, memory)
                if existing is not None and existing.content == memory.content:
                    return RememberResult(status="unchanged", memory=existing)
                raise MemoryConflictError(
                    "The canonical key or an alias belongs to another memory"
                ) from None

    def update(
        self, owner_key: str, memory_id: str, change: MemoryUpdate
    ) -> UserMemory:
        """Replace one owned memory when its expected version is current."""
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._find_by_id(connection, owner_key, memory_id)
                if current is None:
                    raise MemoryConflictError("The requested memory does not exist")
                if current.version != change.expected_version:
                    raise StaleMemoryError(
                        "The memory changed after the requested update was prepared"
                    )

                cursor = connection.execute(
                    """
                    UPDATE user_memories
                    SET display_name = ?, content = ?, version = version + 1,
                        updated_at = ?
                    WHERE owner_key = ? AND id = ? AND version = ?
                    """,
                    (
                        change.display_name,
                        change.content,
                        datetime.now(timezone.utc).isoformat(),
                        owner_key,
                        memory_id,
                        change.expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleMemoryError(
                        "The memory changed while the update was being applied"
                    )

                connection.execute(
                    """
                    DELETE FROM user_memory_aliases
                    WHERE owner_key = ? AND memory_id = ?
                    """,
                    (owner_key, memory_id),
                )
                canonical_display_alias = next(
                    (
                        alias
                        for alias in current.aliases
                        if normalize_memory_key(alias) == current.canonical_key
                    ),
                    current.canonical_key,
                )
                connection.executemany(
                    """
                    INSERT INTO user_memory_aliases (
                        owner_key, normalized_alias, display_alias, memory_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        (owner_key, normalized_alias, display_alias, memory_id)
                        for normalized_alias, display_alias in self._alias_values(
                            canonical_display_alias, change.aliases
                        )
                    ),
                )
                updated = self._find_by_id(connection, owner_key, memory_id)
                if updated is None:
                    raise RuntimeError("Updated user memory could not be re-read")
                connection.commit()
                return updated
            except sqlite3.IntegrityError:
                connection.rollback()
                raise MemoryConflictError(
                    "The canonical key or an alias belongs to another memory"
                ) from None

    def forget(self, owner_key: str, memory_id: str) -> bool:
        """Physically delete one owned memory and its cascading aliases."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM user_memories WHERE owner_key = ? AND id = ?",
                (owner_key, memory_id),
            )
            connection.commit()
            return cursor.rowcount == 1
