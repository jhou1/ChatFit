"""Migrate the explicit legacy 2-1-3 template into durable user memory."""

import argparse
import hashlib
import os
import re
import sqlite3
import stat
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from agents.memory.config import require_distinct_sqlite_files
from agents.memory.models import (
    MemoryConflictError,
    MemoryType,
    NewUserMemory,
    StaleMemoryError,
)
from agents.memory.store import UserMemoryStore, normalize_memory_key, owner_key_for

_TEMPLATE_PATTERN = re.compile(
    r"\A\s*(?P<key>.+?)\s*是一个训练模板\s*"
    r"[（(]\s*你\s*需要记忆一下\s*[）)]\s*[,，]\s*"
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


@dataclass(frozen=True)
class _DestinationAnchor:
    user_path: Path
    parent_path: Path
    name: str
    dir_fd: int
    parent_device: int
    parent_inode: int


@dataclass(frozen=True)
class _StagingDatabase:
    name: str
    state: _FileState


def _filesystem_path(
    raw_value: str, *, role: str, resolve_symlinks: bool = True
) -> Path:
    normalized = raw_value.strip().casefold()
    if (
        not raw_value.strip()
        or normalized.startswith("file:")
        or normalized == ":memory:"
    ):
        raise MigrationError(f"{role} database must be a filesystem path")
    try:
        path = Path(raw_value).expanduser()
        if resolve_symlinks:
            return path.resolve(strict=False)
        return Path(os.path.abspath(path))
    except (OSError, RuntimeError) as error:
        raise MigrationError(f"{role} database path could not be resolved") from error


def _require_dirfd_support() -> None:
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, flag) for flag in required_flags):
        raise MigrationError("secure destination directory access is unsupported")
    if (
        not all(
            function in os.supports_dir_fd
            for function in (os.open, os.stat, os.link, os.unlink)
        )
        or os.stat not in os.supports_follow_symlinks
    ):
        raise MigrationError("secure destination directory access is unsupported")


def _open_directory_without_symlinks(directory_path: Path) -> int:
    _require_dirfd_support()
    parts = directory_path.parts
    if not directory_path.is_absolute() or not parts:
        raise OSError("destination directory path must be absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(parts[0], flags)
    try:
        for component in parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_destination_anchor(destination_path: Path) -> _DestinationAnchor:
    try:
        descriptor = _open_directory_without_symlinks(destination_path.parent)
    except OSError as error:
        raise MigrationError(
            "destination database parent could not be securely opened"
        ) from error
    try:
        parent_state = os.fstat(descriptor)
        if not stat.S_ISDIR(parent_state.st_mode):
            raise MigrationError("destination database parent must be a directory")
        return _DestinationAnchor(
            user_path=destination_path,
            parent_path=destination_path.parent,
            name=destination_path.name,
            dir_fd=descriptor,
            parent_device=parent_state.st_dev,
            parent_inode=parent_state.st_ino,
        )
    except Exception:
        os.close(descriptor)
        raise


def _close_destination_anchor(anchor: _DestinationAnchor | None) -> None:
    if anchor is None:
        return
    try:
        os.close(anchor.dir_fd)
    except OSError as error:
        raise MigrationError(
            "destination database parent could not be closed"
        ) from error


def _require_parent_identity(anchor: _DestinationAnchor) -> None:
    try:
        descriptor = _open_directory_without_symlinks(anchor.parent_path)
        try:
            current = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise MigrationError(
            "destination database parent changed during migration"
        ) from error
    if not stat.S_ISDIR(current.st_mode) or (
        current.st_dev,
        current.st_ino,
    ) != (anchor.parent_device, anchor.parent_inode):
        raise MigrationError("destination database parent changed during migration")


def _physical_parent_path(anchor: _DestinationAnchor) -> Path:
    _require_parent_identity(anchor)
    try:
        if sys.platform == "darwin":
            import fcntl

            raw_path = fcntl.fcntl(anchor.dir_fd, 50, bytes(1024)).split(b"\0", 1)[0]
            physical_path = Path(os.fsdecode(raw_path))
        elif sys.platform.startswith("linux"):
            physical_path = Path(os.readlink(f"/proc/self/fd/{anchor.dir_fd}"))
        else:
            raise MigrationError("secure destination directory access is unsupported")
        physical_state = physical_path.stat()
    except MigrationError:
        raise
    except OSError as error:
        raise MigrationError(
            "destination database parent identity could not be resolved"
        ) from error
    if (physical_state.st_dev, physical_state.st_ino) != (
        anchor.parent_device,
        anchor.parent_inode,
    ):
        raise MigrationError("destination database parent changed during migration")
    return physical_path


def _anchored_entry_path(anchor: _DestinationAnchor, name: str) -> Path:
    return _physical_parent_path(anchor) / name


def _read_only_uri(path: Path) -> str:
    return f"{path.as_uri()}?mode=ro&immutable=1"


def _read_write_uri(path: Path) -> str:
    return f"{path.as_uri()}?mode=rw"


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


def _anchored_file_state(anchor: _DestinationAnchor, name: str) -> _FileState | None:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=anchor.dir_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise MigrationError(
            "destination database metadata could not be read"
        ) from error
    try:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise MigrationError(
            "destination database metadata could not be read"
        ) from error
    finally:
        os.close(descriptor)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise MigrationError("destination database changed while it was inspected")
    return _FileState(
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        size=after.st_size,
        modified_ns=after.st_mtime_ns,
        digest=digest.hexdigest(),
    )


def _destination_snapshot(anchor: _DestinationAnchor) -> _FileFamilySnapshot:
    _require_parent_identity(anchor)
    return tuple(
        _anchored_file_state(anchor, f"{anchor.name}{suffix}")
        for suffix in _FILE_FAMILY_SUFFIXES
    )


def _require_destination_unchanged(
    anchor: _DestinationAnchor, expected: _FileFamilySnapshot
) -> None:
    if _destination_snapshot(anchor) != expected:
        raise MigrationError("destination database changed during migration")


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


def _validate_destination_magic(anchor: _DestinationAnchor) -> None:
    _require_parent_identity(anchor)
    try:
        descriptor = os.open(
            anchor.name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=anchor.dir_fd,
        )
    except FileNotFoundError:
        return
    except OSError as error:
        raise MigrationError("destination database could not be inspected") from error
    try:
        state = os.fstat(descriptor)
        magic = os.read(descriptor, len(_SQLITE_MAGIC))
    except OSError as error:
        raise MigrationError("destination database could not be inspected") from error
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(state.st_mode):
        raise MigrationError("destination database must be a file path")
    if state.st_size > 0 and magic != _SQLITE_MAGIC:
        raise MigrationError("destination database must be a valid SQLite file")


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


def _validate_paths(
    source_db: str, memory_db: str
) -> tuple[Path, Path, _DestinationAnchor | None]:
    source_path = _filesystem_path(source_db, role="source")
    destination_path = _filesystem_path(
        memory_db, role="destination", resolve_symlinks=False
    )
    if not source_path.exists() or not source_path.is_file():
        raise MigrationError("source database must be an existing SQLite file")

    existing_parent = destination_path.parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    if not existing_parent.is_dir():
        raise MigrationError("destination database parent must be a directory")

    anchor: _DestinationAnchor | None = None
    if destination_path.parent.exists():
        anchor = _open_destination_anchor(destination_path)

    try:
        require_distinct_sqlite_files(
            {"source database": source_path, "destination database": destination_path}
        )
        if anchor is not None:
            _validate_destination_magic(anchor)
        _validate_sqlite_magic(source_path, role="source")
    except RuntimeError as error:
        _close_destination_anchor(anchor)
        raise MigrationError(
            "source and destination databases must be distinct"
        ) from error
    except Exception:
        _close_destination_anchor(anchor)
        raise

    return source_path, destination_path, anchor


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
    if normalize_memory_key(match.group("key")) != "2-1-3":
        return None
    definition = match.group("definition").strip()
    if _COMPLETE_DEFINITION_PATTERN.fullmatch(definition) is None:
        return None
    return definition


def _create_staging_database(
    anchor: _DestinationAnchor, expected: _FileFamilySnapshot
) -> _StagingDatabase:
    if expected[0] is not None:
        raise MigrationError("new destination staging requires a missing target")
    _require_checkpointed(expected, role="destination", reject_shm=True)
    _require_destination_unchanged(anchor, expected)
    staging_name: str | None = None
    staging_state: _FileState | None = None
    try:
        for _ in range(10):
            candidate = f".{anchor.name}.migrate-{uuid.uuid4().hex}.db"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=anchor.dir_fd,
                )
                staging_name = candidate
                break
            except FileExistsError:
                continue
        else:
            raise MigrationError("destination staging database name was exhausted")
        try:
            created = os.fstat(descriptor)
            if not stat.S_ISREG(created.st_mode) or created.st_size != 0:
                raise MigrationError(
                    "destination staging database changed during creation"
                )
            staging_state = _FileState(
                device=created.st_dev,
                inode=created.st_ino,
                mode=created.st_mode,
                size=created.st_size,
                modified_ns=created.st_mtime_ns,
                digest=hashlib.sha256().hexdigest(),
            )
        finally:
            os.close(descriptor)
        _require_destination_unchanged(anchor, expected)
        if _anchored_file_state(anchor, staging_name) != staging_state:
            raise MigrationError("destination staging database changed during creation")
        return _StagingDatabase(name=staging_name, state=staging_state)
    except OSError as error:
        if staging_name is not None:
            _cleanup_staging(anchor, staging_name, staging_state)
        raise MigrationError(
            "destination staging database could not be created"
        ) from error
    except Exception:
        if staging_name is not None:
            _cleanup_staging(anchor, staging_name, staging_state)
        raise


def _cleanup_staging(
    anchor: _DestinationAnchor,
    staging_name: str,
    expected: _FileState | None,
) -> None:
    try:
        for suffix in _FILE_FAMILY_SUFFIXES[1:]:
            try:
                os.stat(
                    f"{staging_name}{suffix}",
                    dir_fd=anchor.dir_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            raise MigrationError(
                "destination staging cleanup found a recoverable file; preserved"
            )

        try:
            current = os.stat(
                staging_name,
                dir_fd=anchor.dir_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        is_expected = expected is not None and (
            current.st_dev,
            current.st_ino,
        ) == (expected.device, expected.inode)
        if not is_expected and current.st_nlink <= 1:
            raise MigrationError(
                "destination staging cleanup found a recoverable file; preserved"
            )
        os.unlink(staging_name, dir_fd=anchor.dir_fd)
    except OSError as error:
        raise MigrationError(
            "destination staging database could not be cleaned"
        ) from error


def _install_new_destination(
    anchor: _DestinationAnchor, staging: _StagingDatabase
) -> None:
    """Atomically install a new destination without replacing another writer's file."""
    try:
        os.link(
            staging.name,
            anchor.name,
            src_dir_fd=anchor.dir_fd,
            dst_dir_fd=anchor.dir_fd,
            follow_symlinks=False,
        )
    except FileExistsError as error:
        raise MigrationError(
            "destination database changed during migration; commit conflict"
        ) from error
    except OSError as error:
        raise MigrationError("destination database could not be committed") from error


def _commit_new_staging(
    staging: _StagingDatabase,
    anchor: _DestinationAnchor,
    destination_before: _FileFamilySnapshot,
    source_path: Path,
    source_before: _FileFamilySnapshot,
) -> None:
    _require_unchanged(source_path, source_before, role="source")
    _require_destination_unchanged(anchor, destination_before)
    _require_staging_identity(
        anchor,
        staging,
        source_path,
        require_original_state=False,
    )
    if destination_before[0] is not None:
        raise MigrationError("new destination commit requires a missing target")
    installed = False
    try:
        _install_new_destination(anchor, staging)
        installed = True
        _require_staging_identity(
            anchor,
            staging,
            source_path,
            require_original_state=False,
        )
        installed_state = _anchored_file_state(anchor, anchor.name)
        if installed_state is None or (
            installed_state.device,
            installed_state.inode,
        ) != (staging.state.device, staging.state.inode):
            raise MigrationError(
                "destination staging database changed during installation"
            )
        _require_parent_identity(anchor)
        _require_unchanged(source_path, source_before, role="source")
        _require_parent_identity(anchor)
    except Exception:
        if installed:
            _rollback_new_destination(anchor, staging)
        raise


def _rollback_new_destination(
    anchor: _DestinationAnchor, staging: _StagingDatabase
) -> None:
    destination = _anchored_file_state(anchor, anchor.name)
    staged = _anchored_file_state(anchor, staging.name)
    sidecars = tuple(
        _anchored_file_state(anchor, f"{anchor.name}{suffix}")
        for suffix in _FILE_FAMILY_SUFFIXES[1:]
    )
    destination_matches_created = destination is not None and (
        destination.device,
        destination.inode,
    ) == (staging.state.device, staging.state.inode)
    destination_matches_current_staging = (
        destination is not None
        and staged is not None
        and (destination.device, destination.inode) == (staged.device, staged.inode)
    )
    if (
        destination is None
        or (not destination_matches_created and not destination_matches_current_staging)
        or any(sidecar is not None for sidecar in sidecars)
    ):
        raise MigrationError(
            "destination commit failed; recoverable database was preserved"
        )
    try:
        os.unlink(anchor.name, dir_fd=anchor.dir_fd)
    except OSError as error:
        raise MigrationError(
            "destination commit failed; recoverable database was preserved"
        ) from error


def _require_main_identity(
    anchor: _DestinationAnchor, expected: _FileState, source_path: Path
) -> None:
    _require_parent_identity(anchor)
    current = _anchored_file_state(anchor, anchor.name)
    if current is None or (current.device, current.inode) != (
        expected.device,
        expected.inode,
    ):
        raise MigrationError("destination database changed during migration")
    try:
        require_distinct_sqlite_files(
            {
                "source database": source_path,
                "destination database": _anchored_entry_path(anchor, anchor.name),
            }
        )
    except RuntimeError as error:
        raise MigrationError(
            "source and destination databases must remain distinct"
        ) from error


def _open_verified_destination(
    anchor: _DestinationAnchor, expected: _FileState, source_path: Path
) -> int:
    _require_main_identity(anchor, expected, source_path)
    if _anchored_file_state(anchor, anchor.name) != expected:
        raise MigrationError("destination database changed during migration")
    try:
        descriptor = os.open(
            anchor.name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=anchor.dir_fd,
        )
    except OSError as error:
        raise MigrationError(
            "destination database changed before it could be opened"
        ) from error
    try:
        state = os.fstat(descriptor)
        source_state = source_path.stat()
        if (state.st_dev, state.st_ino) != (expected.device, expected.inode):
            raise MigrationError("destination database changed during migration")
        if (state.st_dev, state.st_ino) == (
            source_state.st_dev,
            source_state.st_ino,
        ):
            raise MigrationError(
                "source and destination databases must remain distinct"
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _memory_value(definition: str) -> NewUserMemory:
    return NewUserMemory(
        memory_type=MemoryType.TRAINING_TEMPLATE,
        canonical_key="213",
        display_name="2-1-3",
        content=definition,
        aliases=_ALIASES,
    )


def _require_dry_run_destination_compatible(
    anchor: _DestinationAnchor,
    source_path: Path,
    source_before: _FileFamilySnapshot,
    user_id: str,
    definition: str,
) -> None:
    destination_before = _destination_snapshot(anchor)
    expected_main = destination_before[0]
    if expected_main is None:
        return
    if any(state is not None for state in destination_before[1:]):
        return

    _require_unchanged(source_path, source_before, role="source")
    _require_destination_unchanged(anchor, destination_before)
    destination_fd = _open_verified_destination(anchor, expected_main, source_path)
    try:
        sqlite_path = _anchored_entry_path(anchor, anchor.name)
        with closing(
            sqlite3.connect(_read_only_uri(sqlite_path), uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only = ON")
            UserMemoryStore.require_reconcile_content_match(
                connection,
                owner_key_for(user_id),
                _memory_value(definition),
            )
    except sqlite3.Error as error:
        raise MigrationError(
            "destination database conflict prevented migration"
        ) from error
    finally:
        os.close(destination_fd)

    _require_main_identity(anchor, expected_main, source_path)
    _require_unchanged(source_path, source_before, role="source")
    _require_destination_unchanged(anchor, destination_before)


def _reconcile_existing_destination(
    anchor: _DestinationAnchor,
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
    _require_destination_unchanged(anchor, destination_before)
    destination_fd = _open_verified_destination(anchor, expected_main, source_path)

    try:
        sqlite_path = _anchored_entry_path(anchor, anchor.name)
        with closing(
            sqlite3.connect(
                _read_write_uri(sqlite_path),
                timeout=2.0,
                uri=True,
            )
        ) as connection:
            _require_main_identity(anchor, expected_main, source_path)
            opened_state = os.fstat(destination_fd)
            if (opened_state.st_dev, opened_state.st_ino) != (
                expected_main.device,
                expected_main.inode,
            ):
                raise MigrationError("destination database changed during migration")
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 2000")
            connection.execute("BEGIN IMMEDIATE")
            try:
                _require_unchanged(source_path, source_before, role="source")
                _require_main_identity(anchor, expected_main, source_path)
                status = UserMemoryStore.reconcile_exact_in_transaction(
                    connection,
                    owner_key_for(user_id),
                    _memory_value(definition),
                )
                _require_unchanged(source_path, source_before, role="source")
                _require_main_identity(anchor, expected_main, source_path)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    except sqlite3.Error as error:
        raise MigrationError(
            "destination database conflict prevented migration"
        ) from error
    finally:
        os.close(destination_fd)

    _require_main_identity(anchor, expected_main, source_path)
    _require_unchanged(source_path, source_before, role="source")
    _require_parent_identity(anchor)
    return status


def _require_staging_identity(
    anchor: _DestinationAnchor,
    staging: _StagingDatabase,
    source_path: Path,
    *,
    require_original_state: bool,
) -> None:
    _require_parent_identity(anchor)
    current = _anchored_file_state(anchor, staging.name)
    if current is None or (current.device, current.inode) != (
        staging.state.device,
        staging.state.inode,
    ):
        raise MigrationError("destination staging database changed during migration")
    if require_original_state and current != staging.state:
        raise MigrationError("destination staging database changed during migration")
    source_state = source_path.stat()
    if (current.device, current.inode) == (
        source_state.st_dev,
        source_state.st_ino,
    ):
        raise MigrationError("source and staging databases must remain distinct")


def _open_verified_staging(
    anchor: _DestinationAnchor,
    staging: _StagingDatabase,
    source_path: Path,
) -> int:
    _require_staging_identity(
        anchor,
        staging,
        source_path,
        require_original_state=True,
    )
    try:
        descriptor = os.open(
            staging.name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=anchor.dir_fd,
        )
    except OSError as error:
        raise MigrationError(
            "destination staging database changed before it could be opened"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (
            staging.state.device,
            staging.state.inode,
        ):
            raise MigrationError(
                "destination staging database changed during migration"
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _apply_memory(
    anchor: _DestinationAnchor,
    staging: _StagingDatabase,
    source_path: Path,
    user_id: str,
    definition: str,
) -> str:
    staging_fd = _open_verified_staging(anchor, staging, source_path)
    try:
        sqlite_path = _anchored_entry_path(anchor, staging.name)
        with closing(
            sqlite3.connect(
                _read_write_uri(sqlite_path),
                timeout=2.0,
                uri=True,
            )
        ) as connection:
            _require_staging_identity(
                anchor,
                staging,
                source_path,
                require_original_state=True,
            )
            opened = os.fstat(staging_fd)
            if (opened.st_dev, opened.st_ino) != (
                staging.state.device,
                staging.state.inode,
            ):
                raise MigrationError(
                    "destination staging database changed during migration"
                )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 2000")
            connection.execute("BEGIN IMMEDIATE")
            try:
                status = UserMemoryStore.reconcile_exact_in_transaction(
                    connection,
                    owner_key_for(user_id),
                    _memory_value(definition),
                )
                _require_staging_identity(
                    anchor,
                    staging,
                    source_path,
                    require_original_state=False,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    finally:
        os.close(staging_fd)
    _require_staging_identity(
        anchor,
        staging,
        source_path,
        require_original_state=False,
    )
    return status


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
    source_path, _, anchor = _validate_paths(args.source_db, args.memory_db)
    try:
        return _run_validated(
            args,
            source_path,
            anchor,
        )
    finally:
        _close_destination_anchor(anchor)


def _run_validated(
    args: argparse.Namespace,
    source_path: Path,
    anchor: _DestinationAnchor | None,
) -> int:
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
    if not definitions:
        return 0

    if not args.apply:
        if anchor is not None:
            _require_dry_run_destination_compatible(
                anchor,
                source_path,
                source_before,
                args.user_id,
                definitions[0],
            )
        return 0

    if anchor is None:
        raise MigrationError("destination database immediate parent must already exist")
    return _apply_validated_definition(
        args,
        source_path,
        source_before,
        anchor,
        definitions[0],
    )


def _apply_validated_definition(
    args: argparse.Namespace,
    source_path: Path,
    source_before: _FileFamilySnapshot,
    anchor: _DestinationAnchor,
    definition: str,
) -> int:
    destination_before = _destination_snapshot(anchor)
    if destination_before[0] is not None:
        status = _reconcile_existing_destination(
            anchor,
            destination_before,
            source_path,
            source_before,
            args.user_id,
            definition,
        )
        print(f"status={status}")
        return 0

    staging: _StagingDatabase | None = None
    try:
        staging = _create_staging_database(anchor, destination_before)
        _require_parent_identity(anchor)
        status = _apply_memory(
            anchor,
            staging,
            source_path,
            args.user_id,
            definition,
        )
        _commit_new_staging(
            staging,
            anchor,
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
        if staging is not None:
            _cleanup_staging(anchor, staging.name, staging.state)
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
