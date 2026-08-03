"""Configuration guards for SQLite files with distinct persistence roles."""

import os
from collections.abc import Mapping
from itertools import combinations
from pathlib import Path
from urllib.parse import parse_qs


def _is_sqlite_memory_target(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized == ":memory:":
        return True
    if not normalized.startswith("file:"):
        return False
    location, _, query = normalized.partition("?")
    if location == "file::memory:":
        return True
    return "memory" in parse_qs(query).get("mode", ())


def resolve_sqlite_file_path(
    value: str | os.PathLike[str], *, setting_name: str
) -> Path:
    """Resolve one durable SQLite target and reject non-file persistence modes."""

    raw_value = os.fspath(value)
    if _is_sqlite_memory_target(raw_value):
        raise RuntimeError(f"{setting_name} must be an on-disk SQLite file")

    db_path = Path(raw_value).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists() and not db_path.is_file():
        raise RuntimeError(
            f"{setting_name} must be a file path, not a directory: {db_path}"
        )
    return db_path.resolve(strict=False)


def require_distinct_sqlite_files(paths: Mapping[str, Path]) -> None:
    """Reject path aliases that would mix databases with different roles."""

    canonical = {
        name: path.expanduser().resolve(strict=False) for name, path in paths.items()
    }
    for (left_name, left_path), (right_name, right_path) in combinations(
        canonical.items(), 2
    ):
        same_file = left_path == right_path
        if not same_file and left_path.exists() and right_path.exists():
            same_file = os.path.samefile(left_path, right_path)
        if same_file:
            raise RuntimeError(
                "SQLite databases must use distinct physical files: "
                f"{left_name} and {right_name} resolve to {left_path}"
            )
