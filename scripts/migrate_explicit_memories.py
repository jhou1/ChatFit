"""Migrate the explicit legacy 2-1-3 template into durable user memory."""

import argparse
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from agents.memory.config import require_distinct_sqlite_files
from agents.memory.models import (
    MemoryConflictError,
    MemoryType,
    MemoryUpdate,
    NewUserMemory,
)
from agents.memory.store import UserMemoryStore, normalize_memory_key, owner_key_for

_EXPLICIT_MARKERS = ("记住", "需要记忆")
_TEMPLATE_PATTERN = re.compile(
    r"\A\s*(?P<key>.+?)\s*是一个训练模板(?P<preamble>.*?)"
    r"它代表\s*(?P<definition>\S.*?)\s*\Z",
    re.DOTALL,
)
_ALIASES = ("213", "2-1-3", "壶铃213")


class MigrationError(Exception):
    """Raised for a safe, user-facing migration failure."""


def _filesystem_path(raw_value: str, *, role: str) -> Path:
    normalized = raw_value.strip().casefold()
    if (
        not raw_value.strip()
        or normalized.startswith("file:")
        or normalized == ":memory:"
    ):
        raise MigrationError(f"{role} database must be a filesystem path")
    return Path(raw_value).expanduser().resolve(strict=False)


def _read_only_uri(path: Path) -> str:
    return f"{path.as_uri()}?mode=ro"


def _validate_paths(source_db: str, memory_db: str) -> tuple[Path, Path]:
    source_path = _filesystem_path(source_db, role="source")
    destination_path = _filesystem_path(memory_db, role="destination")
    if not source_path.exists() or not source_path.is_file():
        raise MigrationError("source database must be an existing SQLite file")
    if destination_path.exists() and not destination_path.is_file():
        raise MigrationError("destination database must be a file path")

    existing_parent = destination_path.parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    if not existing_parent.is_dir():
        raise MigrationError("destination database parent must be a directory")

    try:
        require_distinct_sqlite_files(
            {"source database": source_path, "destination database": destination_path}
        )
    except RuntimeError as error:
        raise MigrationError(
            "source and destination databases must be distinct"
        ) from error

    if destination_path.exists():
        try:
            with closing(
                sqlite3.connect(_read_only_uri(destination_path), uri=True)
            ) as connection:
                connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        except sqlite3.Error as error:
            raise MigrationError(
                "destination database must be a valid SQLite file"
            ) from error
    return source_path, destination_path


def _scan_notes(source_path: Path) -> list[str]:
    try:
        with closing(
            sqlite3.connect(_read_only_uri(source_path), uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute("""
                SELECT DISTINCT note
                FROM training_sessions
                WHERE note IS NOT NULL
                  AND (instr(note, '记住') > 0 OR instr(note, '需要记忆') > 0)
                ORDER BY note
                """).fetchall()
    except sqlite3.Error as error:
        raise MigrationError(
            "source database is not a supported SQLite training database"
        ) from error
    return [row[0] for row in rows]


def _extract_definition(note: str) -> str | None:
    match = _TEMPLATE_PATTERN.fullmatch(note)
    if match is None:
        return None
    explicit_preamble = match.group("preamble")
    if not any(marker in explicit_preamble for marker in _EXPLICIT_MARKERS):
        return None
    if normalize_memory_key(match.group("key")) != "2-1-3":
        return None
    return match.group("definition").strip()


def _apply_memory(destination_path: Path, user_id: str, definition: str) -> str:
    owner_key = owner_key_for(user_id)
    memory = NewUserMemory(
        memory_type=MemoryType.TRAINING_TEMPLATE,
        canonical_key="213",
        display_name="2-1-3",
        content=definition,
        aliases=_ALIASES,
    )
    store = UserMemoryStore(destination_path)
    existing = next(
        (
            item
            for item in store.list_memories(owner_key)
            if item.memory_type == memory.memory_type
            and item.canonical_key == memory.canonical_key
        ),
        None,
    )
    if existing is None:
        return store.remember(owner_key, memory).status
    if (
        existing.content == definition
        and existing.display_name == memory.display_name
        and set(existing.aliases) == set(memory.aliases)
    ):
        return store.remember(owner_key, memory).status
    store.update(
        owner_key,
        existing.id,
        MemoryUpdate(
            display_name=memory.display_name,
            content=memory.content,
            aliases=memory.aliases,
            expected_version=existing.version,
        ),
    )
    return "updated"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate explicit legacy ChatFit memories without modifying the source"
    )
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--memory-db", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    if not args.user_id.strip():
        raise MigrationError("user ID must not be empty")
    source_path, destination_path = _validate_paths(args.source_db, args.memory_db)
    notes = _scan_notes(source_path)
    definitions = [
        definition
        for note in notes
        if (definition := _extract_definition(note)) is not None
    ]
    unrecognized_count = len(notes) - len(definitions)

    print(f"mode={'apply' if args.apply else 'dry-run'}")
    print(f"recognized={len(definitions)}")
    print(f"unrecognized={unrecognized_count}")
    if definitions:
        print("candidate recognized: training_template:213")

    unique_definitions = set(definitions)
    if len(unique_definitions) > 1:
        raise MigrationError("conflicting explicit definitions were found")
    if not definitions or not args.apply:
        return 0

    try:
        status = _apply_memory(destination_path, args.user_id, definitions[0])
    except (MemoryConflictError, sqlite3.Error, OSError, ValueError) as error:
        raise MigrationError(
            "destination database conflict prevented migration"
        ) from error
    print(f"status={status}")
    return 0


def main() -> int:
    try:
        return run(_build_parser().parse_args())
    except MigrationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
