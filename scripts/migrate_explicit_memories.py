"""Migrate the explicit legacy 2-1-3 template into durable user memory."""

import argparse
import hashlib
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from agents.memory.config import require_distinct_sqlite_files
from agents.memory.models import (
    MemoryConflictError,
    MemoryType,
    MemoryUpdate,
    NewUserMemory,
    StaleMemoryError,
)
from agents.memory.store import UserMemoryStore, normalize_memory_key, owner_key_for

_POSITIVE_MARKERS = ("请记住", "需要记忆", "记住")
_NEGATIVE_MARKERS = ("不要记住", "无需记忆", "别记住")
_TEMPLATE_PATTERN = re.compile(
    r"\A\s*(?P<key>.+?)\s*是一个训练模板(?P<preamble>.*?)"
    r"它代表\s*(?P<definition>\S.*?)\s*\Z",
    re.DOTALL,
)
_ALIASES = ("213", "2-1-3", "壶铃213")
_SQLITE_MAGIC = b"SQLite format 3\x00"
_FILE_FAMILY_SUFFIXES = ("", "-wal", "-shm", "-journal")
_DEFINITION_SEPARATOR = r"(?:\s*[,，、。；;]\s*|\s+)"
_COMPLETE_DEFINITION_PATTERN = re.compile(
    r"\A\s*[：:]?\s*"
    r"2\s*个抓举"
    + _DEFINITION_SEPARATOR
    + r"1\s*个挺举"
    + _DEFINITION_SEPARATOR
    + r"3\s*个长循环"
    + _DEFINITION_SEPARATOR
    + r"第一分钟左手一次"
    + _DEFINITION_SEPARATOR
    + r"第二分钟右手一次"
    + _DEFINITION_SEPARATOR
    + r"第三分钟双手一次"
    + _DEFINITION_SEPARATOR
    + r"(?:然后是\s*)?10\s*个波比跳"
    + _DEFINITION_SEPARATOR
    + r"(?:和\s*)?左右手各两次\s*thruster\s*[。.!！]?\s*\Z",
    re.IGNORECASE,
)


class MigrationError(Exception):
    """Raised for a safe, user-facing migration failure."""


@dataclass(frozen=True)
class _FileState:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    digest: str


_FileFamilySnapshot = tuple[_FileState | None, ...]


def _filesystem_path(raw_value: str, *, role: str) -> Path:
    normalized = raw_value.strip().casefold()
    if (
        not raw_value.strip()
        or normalized.startswith("file:")
        or normalized == ":memory:"
    ):
        raise MigrationError(f"{role} database must be a filesystem path")
    try:
        return Path(raw_value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise MigrationError(f"{role} database path could not be resolved") from error


def _read_only_uri(path: Path) -> str:
    return f"{path.as_uri()}?mode=ro&immutable=1"


def _file_state(path: Path) -> _FileState | None:
    try:
        with path.open("rb") as file:
            before = os.fstat(file.fileno())
            digest = hashlib.sha256()
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(file.fileno())
    except FileNotFoundError:
        return None
    except OSError as error:
        raise MigrationError("database file metadata could not be read") from error
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise MigrationError("database file changed while it was being inspected")
    return _FileState(
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        size=after.st_size,
        modified_ns=after.st_mtime_ns,
        digest=digest.hexdigest(),
    )


def _file_family_snapshot(path: Path) -> _FileFamilySnapshot:
    return tuple(
        _file_state(Path(f"{path}{suffix}")) for suffix in _FILE_FAMILY_SUFFIXES
    )


def _require_unchanged(path: Path, expected: _FileFamilySnapshot, *, role: str) -> None:
    if _file_family_snapshot(path) != expected:
        raise MigrationError(f"{role} database changed during migration")


def _validate_sqlite_magic(path: Path, *, role: str) -> None:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    except OSError as error:
        raise MigrationError(f"{role} database could not be inspected") from error
    try:
        with path.open("rb") as file:
            magic = file.read(len(_SQLITE_MAGIC))
    except OSError as error:
        raise MigrationError(f"{role} database could not be inspected") from error
    if size > 0 and magic != _SQLITE_MAGIC:
        raise MigrationError(f"{role} database must be a valid SQLite file")


def _require_checkpointed(
    snapshot: _FileFamilySnapshot, *, role: str, reject_shm: bool
) -> None:
    wal_state = snapshot[1]
    shm_state = snapshot[2]
    journal_state = snapshot[3]
    if wal_state is not None and wal_state.size > 0:
        raise MigrationError(f"{role} database has an active WAL; checkpoint it first")
    if journal_state is not None and journal_state.size > 0:
        raise MigrationError(
            f"{role} database has an active journal; close writers first"
        )
    if reject_shm and (wal_state is not None or shm_state is not None):
        raise MigrationError(
            f"{role} database has SQLite sidecars; checkpoint and close it first"
        )


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

    _validate_sqlite_magic(source_path, role="source")
    _validate_sqlite_magic(destination_path, role="destination")
    return source_path, destination_path


def _scan_notes(source_path: Path) -> list[object]:
    try:
        with closing(
            sqlite3.connect(_read_only_uri(source_path), uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute("""
                SELECT DISTINCT note
                FROM training_sessions
                WHERE note IS NOT NULL
                  AND (
                      instr(CAST(note AS TEXT), '记住') > 0
                      OR instr(CAST(note AS TEXT), '需要记忆') > 0
                      OR instr(CAST(note AS TEXT), '无需记忆') > 0
                  )
                ORDER BY note
                """).fetchall()
    except sqlite3.Error as error:
        raise MigrationError(
            "source database is not a supported SQLite training database"
        ) from error
    return [row[0] for row in rows]


def _extract_definition(note: object) -> str | None:
    if not isinstance(note, str):
        return None
    match = _TEMPLATE_PATTERN.fullmatch(note)
    if match is None:
        return None
    explicit_preamble = match.group("preamble")
    if any(marker in explicit_preamble for marker in _NEGATIVE_MARKERS):
        return None
    if not any(marker in explicit_preamble for marker in _POSITIVE_MARKERS):
        return None
    if normalize_memory_key(match.group("key")) != "2-1-3":
        return None
    definition = match.group("definition").strip()
    if _COMPLETE_DEFINITION_PATTERN.fullmatch(definition) is None:
        return None
    return definition


def _create_staging_database(
    destination_path: Path, expected: _FileFamilySnapshot
) -> Path:
    if expected[0] is not None:
        raise MigrationError("new destination staging requires a missing target")
    _require_checkpointed(expected, role="destination", reject_shm=True)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    _require_unchanged(destination_path, expected, role="destination")
    staging_path: Path | None = None
    try:
        descriptor, staging_name = tempfile.mkstemp(
            prefix=f".{destination_path.name}.migrate-",
            suffix=".db",
            dir=destination_path.parent,
        )
        staging_path = Path(staging_name)
        os.close(descriptor)
        _require_unchanged(destination_path, expected, role="destination")
        return staging_path
    except OSError as error:
        if staging_path is not None:
            _cleanup_staging(staging_path)
        raise MigrationError(
            "destination staging database could not be created"
        ) from error
    except Exception:
        if staging_path is not None:
            _cleanup_staging(staging_path)
        raise


def _cleanup_staging(staging_path: Path) -> None:
    try:
        for suffix in _FILE_FAMILY_SUFFIXES:
            Path(f"{staging_path}{suffix}").unlink(missing_ok=True)
    except OSError as error:
        raise MigrationError(
            "destination staging database could not be cleaned"
        ) from error


def _install_new_destination(staging_path: Path, destination_path: Path) -> None:
    """Atomically install a new destination without replacing another writer's file."""
    try:
        os.link(staging_path, destination_path, follow_symlinks=False)
    except FileExistsError as error:
        raise MigrationError(
            "destination database changed during migration; commit conflict"
        ) from error
    except OSError as error:
        raise MigrationError("destination database could not be committed") from error


def _commit_new_staging(
    staging_path: Path,
    destination_path: Path,
    destination_before: _FileFamilySnapshot,
    source_path: Path,
    source_before: _FileFamilySnapshot,
) -> None:
    _require_unchanged(source_path, source_before, role="source")
    _require_unchanged(destination_path, destination_before, role="destination")
    try:
        require_distinct_sqlite_files(
            {
                "source database": source_path,
                "destination database": destination_path,
            }
        )
    except RuntimeError as error:
        raise MigrationError(
            "source and destination databases must remain distinct"
        ) from error
    if destination_before[0] is not None:
        raise MigrationError("new destination commit requires a missing target")
    _install_new_destination(staging_path, destination_path)
    _require_unchanged(source_path, source_before, role="source")


def _require_main_identity(
    destination_path: Path, expected: _FileState, source_path: Path
) -> None:
    current = _file_state(destination_path)
    if current is None or (current.device, current.inode) != (
        expected.device,
        expected.inode,
    ):
        raise MigrationError("destination database changed during migration")
    try:
        require_distinct_sqlite_files(
            {
                "source database": source_path,
                "destination database": destination_path,
            }
        )
    except RuntimeError as error:
        raise MigrationError(
            "source and destination databases must remain distinct"
        ) from error


def _memory_value(definition: str) -> NewUserMemory:
    return NewUserMemory(
        memory_type=MemoryType.TRAINING_TEMPLATE,
        canonical_key="213",
        display_name="2-1-3",
        content=definition,
        aliases=_ALIASES,
    )


def _reconcile_existing_destination(
    destination_path: Path,
    destination_before: _FileFamilySnapshot,
    source_path: Path,
    source_before: _FileFamilySnapshot,
    user_id: str,
    definition: str,
) -> str:
    expected_main = destination_before[0]
    if expected_main is None:
        raise MigrationError("existing destination reconcile requires a database")
    _require_checkpointed(destination_before, role="destination", reject_shm=True)
    _require_unchanged(source_path, source_before, role="source")
    _require_unchanged(destination_path, destination_before, role="destination")

    try:
        with closing(sqlite3.connect(destination_path, timeout=2.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 2000")
            connection.execute("BEGIN IMMEDIATE")
            try:
                _require_unchanged(source_path, source_before, role="source")
                _require_main_identity(destination_path, expected_main, source_path)
                status = UserMemoryStore.reconcile_exact_in_transaction(
                    connection,
                    owner_key_for(user_id),
                    _memory_value(definition),
                )
                _require_unchanged(source_path, source_before, role="source")
                _require_main_identity(destination_path, expected_main, source_path)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    except sqlite3.Error as error:
        raise MigrationError(
            "destination database conflict prevented migration"
        ) from error

    _require_main_identity(destination_path, expected_main, source_path)
    _require_unchanged(source_path, source_before, role="source")
    return status


def _apply_memory(destination_path: Path, user_id: str, definition: str) -> str:
    owner_key = owner_key_for(user_id)
    memory = _memory_value(definition)
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


def _run(args: argparse.Namespace) -> int:
    if not args.user_id.strip():
        raise MigrationError("user ID must not be empty")
    source_path, destination_path = _validate_paths(args.source_db, args.memory_db)
    source_before = _file_family_snapshot(source_path)
    _require_checkpointed(source_before, role="source", reject_shm=False)
    notes = _scan_notes(source_path)
    _require_unchanged(source_path, source_before, role="source")
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

    destination_before = _file_family_snapshot(destination_path)
    if destination_before[0] is not None:
        status = _reconcile_existing_destination(
            destination_path,
            destination_before,
            source_path,
            source_before,
            args.user_id,
            definitions[0],
        )
        print(f"status={status}")
        return 0

    staging_path: Path | None = None
    try:
        staging_path = _create_staging_database(destination_path, destination_before)
        status = _apply_memory(staging_path, args.user_id, definitions[0])
        _commit_new_staging(
            staging_path,
            destination_path,
            destination_before,
            source_path,
            source_before,
        )
    except (
        MemoryConflictError,
        StaleMemoryError,
        sqlite3.Error,
        OSError,
        ValueError,
    ) as error:
        raise MigrationError(
            "destination database conflict prevented migration"
        ) from error
    finally:
        if staging_path is not None:
            _cleanup_staging(staging_path)
    print(f"status={status}")
    return 0


def run(args: argparse.Namespace) -> int:
    try:
        return _run(args)
    except MigrationError:
        raise
    except (MemoryConflictError, StaleMemoryError) as error:
        raise MigrationError(
            "destination database conflict prevented migration"
        ) from error
    except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as error:
        raise MigrationError("migration failed safely") from error


def main() -> int:
    try:
        return run(_build_parser().parse_args())
    except MigrationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
