import argparse
from contextlib import closing
import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.migrate_explicit_memories as migration_module
from agents.memory.models import MemoryType, NewUserMemory
from agents.memory.models import StaleMemoryError
from agents.memory.store import UserMemoryStore, owner_key_for

HISTORICAL_NOTE = (
    "2-1-3\n"
    "是一个训练模板（你需要记忆一下），它代表2个抓举，1个挺举，3个长循环。\n"
    "第一分钟左手一次，第二分钟右手一次，第三分钟双手一次，然后是10个波比跳\n"
    "和左右手各两次 thruster。"
)
EXPECTED_DEFINITION = (
    "2个抓举，1个挺举，3个长循环。\n"
    "第一分钟左手一次，第二分钟右手一次，第三分钟双手一次，然后是10个波比跳\n"
    "和左右手各两次 thruster。"
)


def _create_source(path: Path, notes: list[object]) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE training_sessions (id INTEGER PRIMARY KEY, note TEXT)"
        )
        connection.executemany(
            "INSERT INTO training_sessions (note) VALUES (?)",
            ((note,) for note in notes),
        )


def _run_migration(
    source_db: Path, memory_db: Path, *, apply: bool = False, user_id: str = "user-a"
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "scripts.migrate_explicit_memories",
        "--source-db",
        os.fspath(source_db),
        "--memory-db",
        os.fspath(memory_db),
        "--user-id",
        user_id,
    ]
    if apply:
        command.append("--apply")
    return subprocess.run(command, capture_output=True, check=False, text=True)


def _source_snapshot(path: Path) -> tuple[bytes, list[tuple], list[tuple]]:
    file_bytes = path.read_bytes()
    with closing(sqlite3.connect(path)) as connection, connection:
        schema = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        rows = connection.execute(
            "SELECT id, note FROM training_sessions ORDER BY id"
        ).fetchall()
    return file_bytes, schema, rows


def _memory_rows(path: Path) -> tuple[list[tuple], list[tuple]]:
    with closing(sqlite3.connect(path)) as connection, connection:
        memories = connection.execute("""
            SELECT id, owner_key, memory_type, canonical_key, display_name,
                   content, version
            FROM user_memories
            ORDER BY id
            """).fetchall()
        aliases = connection.execute("""
            SELECT normalized_alias, display_alias, memory_id
            FROM user_memory_aliases
            ORDER BY normalized_alias
            """).fetchall()
    return memories, aliases


def _file_family_snapshot(path: Path) -> dict[str, tuple[int, int, str] | None]:
    snapshot: dict[str, tuple[int, int, str] | None] = {}
    for suffix in ("", "-wal", "-shm"):
        member = Path(f"{path}{suffix}")
        if not member.exists():
            snapshot[suffix] = None
            continue
        stat = member.stat()
        snapshot[suffix] = (
            stat.st_size,
            stat.st_mtime_ns,
            hashlib.sha256(member.read_bytes()).hexdigest(),
        )
    return snapshot


def _create_active_wal_database(
    path: Path, statements: tuple[str, ...]
) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
    for statement in statements:
        connection.execute(statement)
    connection.commit()
    shm_path = Path(f"{path}-shm")
    assert Path(f"{path}-wal").stat().st_size > 0
    assert shm_path.exists()
    shm_path.unlink()
    return connection


def _run_args(source_db: Path, memory_db: Path) -> argparse.Namespace:
    return argparse.Namespace(
        source_db=os.fspath(source_db),
        memory_db=os.fspath(memory_db),
        user_id="user-a",
        apply=True,
    )


def test_dry_run_reports_candidates_without_writing_or_leaking_notes(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    private_unrecognized = "记住我的私人健康情况，但这不是模板定义"
    _create_source(
        source_db,
        [
            HISTORICAL_NOTE,
            HISTORICAL_NOTE,
            "今天完成了普通 213 训练",
            private_unrecognized,
        ],
    )
    before = _source_snapshot(source_db)

    first = _run_migration(source_db, memory_db)
    second = _run_migration(source_db, memory_db)

    assert first.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    assert "mode=dry-run" in first.stdout
    assert "recognized=1" in first.stdout
    assert "unrecognized=1" in first.stdout
    assert "training_template:213" in first.stdout
    assert EXPECTED_DEFINITION not in first.stdout
    assert private_unrecognized not in first.stdout
    assert not memory_db.exists()
    assert _source_snapshot(source_db) == before


@pytest.mark.parametrize(
    "negative_marker",
    [
        "不需要记忆",
        "不必记住",
        "没有让你记住",
        "请不要记住",
        "无需记忆",
        "别记住",
    ],
)
def test_negative_memory_markers_are_unrecognized(tmp_path, negative_marker):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    note = HISTORICAL_NOTE.replace("你需要记忆一下", negative_marker)
    _create_source(source_db, [note])

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode == 0, result.stderr
    assert "recognized=0" in result.stdout
    assert "unrecognized=1" in result.stdout
    assert note not in result.stdout
    assert not memory_db.exists()


@pytest.mark.parametrize(
    "partial_definition",
    [
        "2个抓举",
        "2个抓举，1个挺举，3个长循环。",
        (
            "2个抓举，1个挺举，3个长循环。第一分钟左手一次，"
            "第二分钟右手一次，第三分钟双手一次，然后是10个波比跳。"
        ),
    ],
)
def test_partial_legacy_definitions_are_unrecognized(tmp_path, partial_definition):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    note = f"2-1-3 是一个训练模板（你需要记忆一下），它代表{partial_definition}"
    _create_source(source_db, [note])

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode == 0, result.stderr
    assert "recognized=0" in result.stdout
    assert "unrecognized=1" in result.stdout
    assert note not in result.stdout
    assert not memory_db.exists()


@pytest.mark.parametrize(
    ("original", "incorrect"),
    [
        ("2个抓举", "12个抓举"),
        ("1个挺举", "2个挺举"),
        ("3个长循环", "30个长循环"),
        ("10个波比跳", "11个波比跳"),
        ("左右手各两次 thruster", "左右手各三次 thruster"),
    ],
)
def test_incorrect_component_counts_are_unrecognized(tmp_path, original, incorrect):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    note = HISTORICAL_NOTE.replace(original, incorrect)
    _create_source(source_db, [note])

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode == 0, result.stderr
    assert "recognized=0" in result.stdout
    assert "unrecognized=1" in result.stdout
    assert not memory_db.exists()


@pytest.mark.parametrize(
    ("anchor", "contradiction"),
    [
        ("2个抓举，", "2个挺举（错误），"),
        ("3个长循环。\n", "第一分钟右手一次（错误），"),
    ],
)
def test_contradictory_extra_components_are_unrecognized(
    tmp_path, anchor, contradiction
):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    note = HISTORICAL_NOTE.replace(anchor, f"{anchor}{contradiction}")
    _create_source(source_db, [note])

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode == 0, result.stderr
    assert "recognized=0" in result.stdout
    assert "unrecognized=1" in result.stdout
    assert note not in result.stdout
    assert not memory_db.exists()


def test_complete_definition_accepts_reasonable_spacing_case_and_punctuation(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    note = (
        "2 - 1 - 3 是一个训练模板 ( 你需要记忆一下 )，它代表：2个抓举、1个挺举、"
        "3个长循环；第一分钟左手一次，第二分钟右手一次，第三分钟双手一次；"
        "然后是10个波比跳，和左右手各两次 THRUSTER。"
    )
    _create_source(source_db, [note])

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode == 0, result.stderr
    assert "recognized=1" in result.stdout
    memories, _ = _memory_rows(memory_db)
    assert memories[0][5].startswith("：2个抓举、1个挺举、3个长循环")


def test_source_with_active_wal_fails_without_creating_or_changing_sidecars(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "nested" / "memory.db"
    connection = _create_active_wal_database(
        source_db,
        (
            "CREATE TABLE training_sessions (id INTEGER PRIMARY KEY, note TEXT)",
            "INSERT INTO training_sessions (note) VALUES ('记住私人 WAL 内容')",
        ),
    )
    before = _file_family_snapshot(source_db)
    assert before["-shm"] is None

    try:
        result = _run_migration(source_db, memory_db)
        after = _file_family_snapshot(source_db)
    finally:
        connection.close()

    assert result.returncode != 0
    assert "wal" in result.stderr.lower()
    assert "私人 WAL 内容" not in result.stderr
    assert after == before
    assert not memory_db.parent.exists()


def test_apply_requires_existing_immediate_parent_without_creating_tree(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "missing-a" / "missing-b" / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    source_before = _source_snapshot(source_db)
    tree_before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode != 0
    assert "parent" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == tree_before
    assert _source_snapshot(source_db) == source_before


def test_dry_run_reports_candidate_without_creating_missing_parent_tree(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "missing-a" / "missing-b" / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    tree_before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    result = _run_migration(source_db, memory_db)

    assert result.returncode == 0, result.stderr
    assert "recognized=1" in result.stdout
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == tree_before


def test_apply_never_calls_mkdir_for_missing_destination_parent(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "missing-a" / "missing-b" / "memory.db"
    redirect_parent = tmp_path / "redirect-parent"
    _create_source(source_db, [HISTORICAL_NOTE])
    redirect_parent.mkdir()
    mkdir_called = False

    def redirecting_mkdir(path, *_args, **_kwargs):
        nonlocal mkdir_called
        mkdir_called = True
        Path(path).symlink_to(redirect_parent, target_is_directory=True)

    monkeypatch.setattr(Path, "mkdir", redirecting_mkdir)

    with pytest.raises(migration_module.MigrationError, match="parent"):
        migration_module.run(_run_args(source_db, memory_db))

    assert not mkdir_called
    assert not (redirect_parent / "memory.db").exists()
    assert not memory_db.parent.exists()


@pytest.mark.parametrize("symlink_position", ["immediate-parent", "ancestor"])
def test_apply_rejects_symlink_in_any_destination_parent_component(
    tmp_path, symlink_position
) -> None:
    source_db = tmp_path / "source.db"
    real_parent = tmp_path / "real-parent"
    linked_parent = tmp_path / "linked-parent"
    _create_source(source_db, [HISTORICAL_NOTE])
    real_parent.mkdir()
    if symlink_position == "immediate-parent":
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        memory_db = linked_parent / "memory.db"
        redirected_db = real_parent / "memory.db"
    else:
        (real_parent / "nested").mkdir()
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        memory_db = linked_parent / "nested" / "memory.db"
        redirected_db = real_parent / "nested" / "memory.db"

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode != 0
    assert "parent" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert not redirected_db.exists()


def test_dry_run_only_stats_wal_destination_without_creating_shm(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    connection = _create_active_wal_database(
        memory_db,
        (
            "CREATE TABLE private_values (value TEXT)",
            "INSERT INTO private_values VALUES ('private destination value')",
        ),
    )
    before = _file_family_snapshot(memory_db)
    assert before["-shm"] is None

    try:
        result = _run_migration(source_db, memory_db)
        after = _file_family_snapshot(memory_db)
    finally:
        connection.close()

    assert result.returncode == 0, result.stderr
    assert "private destination value" not in result.stdout
    assert after == before


def test_apply_rejects_active_destination_wal_without_changing_sidecars(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    connection = _create_active_wal_database(
        memory_db,
        (
            "CREATE TABLE private_values (value TEXT)",
            "INSERT INTO private_values VALUES ('private destination value')",
        ),
    )
    before = _file_family_snapshot(memory_db)

    try:
        result = _run_migration(source_db, memory_db, apply=True)
        after = _file_family_snapshot(memory_db)
    finally:
        connection.close()

    assert result.returncode != 0
    assert "wal" in result.stderr.lower()
    assert "private destination value" not in result.stdout + result.stderr
    assert after == before


def test_apply_is_idempotent_and_preserves_source_bytes_schema_and_rows(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE, "今天完成了普通 213 训练"])
    before = _source_snapshot(source_db)

    first = _run_migration(source_db, memory_db, apply=True)
    first_rows = _memory_rows(memory_db)
    second = _run_migration(source_db, memory_db, apply=True)
    second_rows = _memory_rows(memory_db)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "status=created" in first.stdout
    assert "status=unchanged" in second.stdout
    assert first_rows == second_rows
    memories, aliases = second_rows
    assert memories == [
        (
            memories[0][0],
            owner_key_for("user-a"),
            "training_template",
            "213",
            "2-1-3",
            EXPECTED_DEFINITION,
            1,
        )
    ]
    assert aliases == [
        ("2-1-3", "2-1-3", memories[0][0]),
        ("213", "213", memories[0][0]),
        ("壶铃213", "壶铃213", memories[0][0]),
    ]
    assert _source_snapshot(source_db) == before


@pytest.mark.parametrize("apply", (False, True), ids=("dry-run", "apply"))
def test_existing_different_content_is_a_non_mutating_conflict(tmp_path, apply):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    store = UserMemoryStore(memory_db)
    original = store.remember(
        owner_key_for("user-a"),
        NewUserMemory(
            memory_type=MemoryType.TRAINING_TEMPLATE,
            canonical_key="213",
            display_name="旧版 213",
            content="旧版模板内容",
            aliases=("213", "2-1-3", "壶铃213"),
        ),
    ).memory
    rows_before = _memory_rows(memory_db)
    files_before = _file_family_snapshot(memory_db)

    result = _run_migration(source_db, memory_db, apply=apply)

    assert result.returncode != 0
    assert "conflict" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert "status=" not in result.stdout
    assert _memory_rows(memory_db) == rows_before
    assert _file_family_snapshot(memory_db) == files_before
    assert rows_before[0][0][0] == original.id


def test_apply_repairs_incomplete_aliases_for_identical_existing_content(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    store = UserMemoryStore(memory_db)
    original = store.remember(
        owner_key_for("user-a"),
        NewUserMemory(
            memory_type=MemoryType.TRAINING_TEMPLATE,
            canonical_key="213",
            display_name="旧版 213",
            content=EXPECTED_DEFINITION,
            aliases=("213",),
        ),
    ).memory

    first = _run_migration(source_db, memory_db, apply=True)
    first_rows = _memory_rows(memory_db)
    second = _run_migration(source_db, memory_db, apply=True)
    second_rows = _memory_rows(memory_db)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "status=updated" in first.stdout
    assert "status=unchanged" in second.stdout
    assert first_rows == second_rows
    memories, aliases = second_rows
    assert len(memories) == 1
    assert memories[0][0] == original.id
    assert memories[0][4:] == ("2-1-3", EXPECTED_DEFINITION, 1)
    assert {alias[1] for alias in aliases} == {"213", "2-1-3", "壶铃213"}


def test_only_complete_explicit_legacy_definition_is_migrated(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    notes = [
        "213 今天练得很顺利",
        "记住 213 是我今天的训练记录",
        "2-1-3 是一个训练模板（你需要记忆一下），但没有定义",
        "其他模板是一个训练模板（请记住），它代表私人内容",
    ]
    _create_source(source_db, notes)

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode == 0, result.stderr
    assert "recognized=0" in result.stdout
    assert "unrecognized=3" in result.stdout
    assert not memory_db.exists()
    for note in notes:
        assert note not in result.stdout


def test_conflicting_source_definitions_fail_before_destination_is_created(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    conflicting = HISTORICAL_NOTE.replace("2个抓举，1个挺举", "2个抓举、1个挺举")
    _create_source(source_db, [HISTORICAL_NOTE, conflicting])
    before = _source_snapshot(source_db)

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode != 0
    assert "conflicting" in result.stderr.lower()
    assert not memory_db.exists()
    assert _source_snapshot(source_db) == before


def test_destination_alias_conflict_fails_without_mutating_existing_memory(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    store = UserMemoryStore(memory_db)
    existing = store.remember(
        owner_key_for("user-a"),
        NewUserMemory(
            memory_type=MemoryType.TRAINING_PREFERENCE,
            canonical_key="other",
            display_name="Other",
            content="existing content",
            aliases=("2-1-3",),
        ),
    ).memory
    before = _memory_rows(memory_db)

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode != 0
    assert "conflict" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert _memory_rows(memory_db) == before
    assert before[0][0][0] == existing.id


@pytest.mark.parametrize("replacement_kind", ["hardlink", "symlink"])
def test_destination_replacement_after_validation_cannot_modify_source(
    tmp_path, monkeypatch, replacement_kind
):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    before = _source_snapshot(source_db)
    real_scan = migration_module._scan_notes

    def scan_then_replace(source_path):
        notes = real_scan(source_path)
        if replacement_kind == "hardlink":
            os.link(source_db, memory_db)
        else:
            memory_db.symlink_to(source_db)
        return notes

    monkeypatch.setattr(migration_module, "_scan_notes", scan_then_replace)

    with pytest.raises(migration_module.MigrationError):
        migration_module.run(_run_args(source_db, memory_db))

    assert _source_snapshot(source_db) == before
    with closing(sqlite3.connect(source_db)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'user_memories'"
        ).fetchone() == (0,)
    assert not list(tmp_path.glob(".memory.db.*"))


@pytest.mark.parametrize(
    "swap_point",
    ["after-validation", "after-staging", "before-staging-sql", "before-install"],
)
def test_destination_parent_swap_never_installs_or_leaks_staging(
    tmp_path, monkeypatch, swap_point
) -> None:
    source_db = tmp_path / "source.db"
    destination_parent = tmp_path / "validated-parent"
    moved_parent = tmp_path / "moved-validated-parent"
    replacement_parent = tmp_path / "replacement-parent"
    memory_db = destination_parent / "memory.db"
    destination_parent.mkdir()
    replacement_parent.mkdir()
    _create_source(source_db, [HISTORICAL_NOTE])

    def swap_parent() -> None:
        destination_parent.rename(moved_parent)
        destination_parent.symlink_to(replacement_parent, target_is_directory=True)

    if swap_point == "after-validation":
        real_scan = migration_module._scan_notes

        def scan_then_swap(source_path):
            notes = real_scan(source_path)
            swap_parent()
            return notes

        monkeypatch.setattr(migration_module, "_scan_notes", scan_then_swap)
    elif swap_point == "after-staging":
        real_create = migration_module._create_staging_database

        def create_then_swap(*args, **kwargs):
            staging = real_create(*args, **kwargs)
            swap_parent()
            return staging

        monkeypatch.setattr(
            migration_module, "_create_staging_database", create_then_swap
        )
    elif swap_point == "before-staging-sql":
        real_apply = migration_module._apply_memory

        def swap_then_apply(*args, **kwargs):
            swap_parent()
            return real_apply(*args, **kwargs)

        monkeypatch.setattr(migration_module, "_apply_memory", swap_then_apply)
    else:
        real_install = migration_module._install_new_destination

        def swap_then_install(*args, **kwargs):
            swap_parent()
            return real_install(*args, **kwargs)

        monkeypatch.setattr(
            migration_module, "_install_new_destination", swap_then_install
        )

    with pytest.raises(migration_module.MigrationError, match="parent|changed"):
        migration_module.run(_run_args(source_db, memory_db))

    assert not (replacement_parent / "memory.db").exists()
    assert not (moved_parent / "memory.db").exists()
    assert not list(replacement_parent.glob(".memory.db.migrate-*.db*"))
    assert not list(moved_parent.glob(".memory.db.migrate-*.db*"))


def test_staging_hardlink_swap_cannot_modify_source_before_sql(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    source_before = _source_snapshot(source_db)
    real_apply = migration_module._apply_memory

    def replace_staging_then_apply(*args, **kwargs):
        first = args[0]
        if isinstance(first, Path):
            first.unlink()
            os.link(source_db, first)
        else:
            anchor = first
            staging = args[1]
            os.unlink(staging.name, dir_fd=anchor.dir_fd)
            os.link(
                source_db,
                staging.name,
                dst_dir_fd=anchor.dir_fd,
                follow_symlinks=False,
            )
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(migration_module, "_apply_memory", replace_staging_then_apply)

    with pytest.raises(migration_module.MigrationError, match="changed|distinct"):
        migration_module.run(_run_args(source_db, memory_db))

    assert _source_snapshot(source_db) == source_before
    with closing(sqlite3.connect(source_db)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'user_memories'"
        ).fetchone() == (0,)
    assert not memory_db.exists()
    assert not list(tmp_path.glob(".memory.db.migrate-*.db*"))


def test_staging_replacement_before_first_path_snapshot_cannot_modify_victim(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    victim_db = tmp_path / "victim.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    with closing(sqlite3.connect(victim_db)) as connection, connection:
        connection.execute("CREATE TABLE private_values (value TEXT)")
        connection.execute("INSERT INTO private_values VALUES ('must survive')")
    victim_before = _file_family_snapshot(victim_db)
    real_state = migration_module._anchored_file_state
    replaced = False

    def replace_before_first_staging_state(anchor, name):
        nonlocal replaced
        if not replaced and name.startswith(".memory.db.migrate-"):
            replaced = True
            os.unlink(name, dir_fd=anchor.dir_fd)
            os.link(
                victim_db,
                name,
                dst_dir_fd=anchor.dir_fd,
                follow_symlinks=False,
            )
        return real_state(anchor, name)

    monkeypatch.setattr(
        migration_module, "_anchored_file_state", replace_before_first_staging_state
    )

    with pytest.raises(migration_module.MigrationError, match="staging.*changed"):
        migration_module.run(_run_args(source_db, memory_db))

    assert replaced
    assert _file_family_snapshot(victim_db) == victim_before
    with closing(sqlite3.connect(victim_db)) as connection, connection:
        assert connection.execute("SELECT * FROM private_values").fetchall() == [
            ("must survive",)
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'user_memories'"
        ).fetchone() == (0,)
    assert not memory_db.exists()
    assert not list(tmp_path.glob(".memory.db.migrate-*.db*"))


def test_cleanup_preserves_unrelated_single_link_replacement(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    victim_db = tmp_path / "victim.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    with closing(sqlite3.connect(victim_db)) as connection, connection:
        connection.execute("CREATE TABLE private_values (value TEXT)")
        connection.execute("INSERT INTO private_values VALUES ('must be recoverable')")
    victim_before = _file_family_snapshot(victim_db)
    real_state = migration_module._anchored_file_state
    replaced = False

    def move_victim_before_first_staging_state(anchor, name):
        nonlocal replaced
        if not replaced and name.startswith(".memory.db.migrate-"):
            replaced = True
            os.unlink(name, dir_fd=anchor.dir_fd)
            os.rename(victim_db, name, dst_dir_fd=anchor.dir_fd)
        return real_state(anchor, name)

    monkeypatch.setattr(
        migration_module,
        "_anchored_file_state",
        move_victim_before_first_staging_state,
    )

    with pytest.raises(
        migration_module.MigrationError, match="preserved|staging.*changed"
    ):
        migration_module.run(_run_args(source_db, memory_db))

    assert replaced
    assert not victim_db.exists()
    preserved = list(tmp_path.glob(".memory.db.migrate-*.db"))
    assert len(preserved) == 1
    assert _file_family_snapshot(preserved[0]) == victim_before
    with closing(sqlite3.connect(preserved[0])) as connection, connection:
        assert connection.execute("SELECT * FROM private_values").fetchall() == [
            ("must be recoverable",)
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'user_memories'"
        ).fetchone() == (0,)
    assert not memory_db.exists()


def test_staging_source_hardlink_swap_before_install_is_rolled_back(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    source_before = _source_snapshot(source_db)
    real_install = migration_module._install_new_destination

    def replace_staging_then_install(anchor, staging):
        os.unlink(staging.name, dir_fd=anchor.dir_fd)
        os.link(
            source_db,
            staging.name,
            dst_dir_fd=anchor.dir_fd,
            follow_symlinks=False,
        )
        return real_install(anchor, staging)

    monkeypatch.setattr(
        migration_module, "_install_new_destination", replace_staging_then_install
    )

    with pytest.raises(migration_module.MigrationError, match="staging|distinct"):
        migration_module.run(_run_args(source_db, memory_db))

    assert _source_snapshot(source_db) == source_before
    assert not memory_db.exists()
    assert not list(tmp_path.glob(".memory.db.migrate-*.db*"))


def test_clean_wal_source_hardlink_swap_fails_before_alias_sidecars(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    with closing(sqlite3.connect(source_db)) as connection, connection:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    Path(f"{source_db}-wal").unlink(missing_ok=True)
    Path(f"{source_db}-shm").unlink(missing_ok=True)
    source_before = _file_family_snapshot(source_db)
    UserMemoryStore(memory_db)
    real_require = migration_module._require_destination_unchanged
    real_require_identity = migration_module._require_main_identity
    destination_checks = 0

    def require_then_swap(anchor, expected):
        nonlocal destination_checks
        real_require(anchor, expected)
        destination_checks += 1
        if destination_checks == 1:
            memory_db.unlink()
            os.link(source_db, memory_db)

    def require_identity_without_alias_sidecars(*args, **kwargs):
        assert not Path(f"{memory_db}-wal").exists()
        assert not Path(f"{memory_db}-shm").exists()
        return real_require_identity(*args, **kwargs)

    monkeypatch.setattr(
        migration_module, "_require_destination_unchanged", require_then_swap
    )
    monkeypatch.setattr(
        migration_module,
        "_require_main_identity",
        require_identity_without_alias_sidecars,
    )

    with pytest.raises(migration_module.MigrationError, match="changed|distinct"):
        migration_module.run(_run_args(source_db, memory_db))

    assert _file_family_snapshot(source_db) == source_before
    assert not Path(f"{memory_db}-wal").exists()
    assert not Path(f"{memory_db}-shm").exists()


def test_existing_destination_removed_after_verified_open_is_not_recreated(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    UserMemoryStore(memory_db)
    real_open = migration_module._open_verified_destination

    def open_then_remove(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        memory_db.unlink()
        return descriptor

    monkeypatch.setattr(
        migration_module, "_open_verified_destination", open_then_remove
    )

    with pytest.raises(migration_module.MigrationError):
        migration_module.run(_run_args(source_db, memory_db))

    assert not memory_db.exists()
    assert not Path(f"{memory_db}-wal").exists()
    assert not Path(f"{memory_db}-shm").exists()


def test_concurrent_destination_creation_is_preserved_and_staging_is_rejected(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    real_apply = migration_module._apply_memory
    concurrent_id: str | None = None

    def apply_then_create_destination(
        anchor, staging, source_path, user_id, definition
    ):
        nonlocal concurrent_id
        status = real_apply(anchor, staging, source_path, user_id, definition)
        concurrent = (
            UserMemoryStore(memory_db)
            .remember(
                owner_key_for(user_id),
                NewUserMemory(
                    memory_type=MemoryType.TRAINING_TEMPLATE,
                    canonical_key="213",
                    display_name="Concurrent 213",
                    content=definition,
                    aliases=("213",),
                ),
            )
            .memory
        )
        concurrent_id = concurrent.id
        return status

    monkeypatch.setattr(
        migration_module, "_apply_memory", apply_then_create_destination
    )

    with pytest.raises(migration_module.MigrationError, match="changed|conflict"):
        migration_module.run(_run_args(source_db, memory_db))

    memories, aliases = _memory_rows(memory_db)
    assert len(memories) == 1
    assert memories[0][0] == concurrent_id
    assert memories[0][4] == "Concurrent 213"
    assert [alias[1] for alias in aliases] == ["213"]
    assert not list(tmp_path.glob(".memory.db.*"))


def test_destination_created_after_final_snapshot_is_not_replaced(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    real_require = migration_module._require_destination_unchanged
    destination_checks = 0
    concurrent_id: str | None = None

    def require_then_create(anchor, expected):
        nonlocal concurrent_id, destination_checks
        real_require(anchor, expected)
        destination_checks += 1
        if destination_checks == 3:
            concurrent_id = (
                UserMemoryStore(memory_db)
                .remember(
                    owner_key_for("concurrent-user"),
                    NewUserMemory(
                        memory_type=MemoryType.PROFILE,
                        canonical_key="concurrent",
                        display_name="Concurrent",
                        content="concurrent destination content",
                        aliases=("concurrent",),
                    ),
                )
                .memory.id
            )

    monkeypatch.setattr(
        migration_module, "_require_destination_unchanged", require_then_create
    )

    with pytest.raises(migration_module.MigrationError, match="changed|conflict"):
        migration_module.run(_run_args(source_db, memory_db))

    with closing(sqlite3.connect(memory_db)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM user_memories WHERE id = ?", (concurrent_id,)
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM user_memories WHERE canonical_key = '213'"
        ).fetchone() == (0,)
    assert not list(tmp_path.glob(".memory.db.*"))


def test_destination_updated_after_snapshot_fails_without_losing_external_change(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    store = UserMemoryStore(memory_db)
    original_id = store.remember(
        owner_key_for("existing-user"),
        NewUserMemory(
            memory_type=MemoryType.PROFILE,
            canonical_key="existing",
            display_name="Existing",
            content="existing destination content",
            aliases=("existing",),
        ),
    ).memory.id
    real_require = migration_module._require_destination_unchanged
    destination_checks = 0
    concurrent_id: str | None = None

    def require_then_update(anchor, expected):
        nonlocal concurrent_id, destination_checks
        real_require(anchor, expected)
        destination_checks += 1
        if destination_checks == 1:
            concurrent_id = store.remember(
                owner_key_for("concurrent-user"),
                NewUserMemory(
                    memory_type=MemoryType.PROFILE,
                    canonical_key="concurrent",
                    display_name="Concurrent",
                    content="concurrent destination content",
                    aliases=("concurrent",),
                ),
            ).memory.id

    monkeypatch.setattr(
        migration_module, "_require_destination_unchanged", require_then_update
    )

    with pytest.raises(migration_module.MigrationError, match="changed"):
        migration_module.run(_run_args(source_db, memory_db))

    with closing(sqlite3.connect(memory_db)) as connection, connection:
        stored_ids = {
            row[0]
            for row in connection.execute("SELECT id FROM user_memories ORDER BY id")
        }
        assert concurrent_id is not None
        assert stored_ids == {original_id, concurrent_id}
        assert connection.execute(
            "SELECT COUNT(*) FROM user_memories WHERE canonical_key = '213'"
        ).fetchone() == (0,)
    assert not list(tmp_path.glob(".memory.db.*"))


def test_final_check_wal_writer_is_serialized_without_losing_either_change(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    store = UserMemoryStore(memory_db)
    original_id = store.remember(
        owner_key_for("existing-user"),
        NewUserMemory(
            memory_type=MemoryType.PROFILE,
            canonical_key="existing",
            display_name="Existing",
            content="before concurrent WAL update",
            aliases=("existing",),
        ),
    ).memory.id
    with closing(sqlite3.connect(memory_db)) as connection, connection:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    Path(f"{memory_db}-wal").unlink(missing_ok=True)
    Path(f"{memory_db}-shm").unlink(missing_ok=True)
    assert not Path(f"{memory_db}-wal").exists()
    assert not Path(f"{memory_db}-shm").exists()

    real_require = migration_module._require_destination_unchanged
    destination_checks = 0
    writer: sqlite3.Connection | None = None

    def require_then_write_wal(anchor, expected):
        nonlocal destination_checks, writer
        real_require(anchor, expected)
        destination_checks += 1
        if destination_checks == 1:
            writer = sqlite3.connect(memory_db)
            writer.execute(
                "UPDATE user_memories SET content = ? WHERE id = ?",
                ("concurrent WAL update survives", original_id),
            )
            writer.commit()
            assert Path(f"{memory_db}-wal").stat().st_size > 0

    monkeypatch.setattr(
        migration_module, "_require_destination_unchanged", require_then_write_wal
    )

    try:
        assert migration_module.run(_run_args(source_db, memory_db)) == 0
    finally:
        if writer is not None:
            writer.close()

    with closing(sqlite3.connect(memory_db)) as connection, connection:
        assert connection.execute(
            "SELECT canonical_key, content FROM user_memories ORDER BY canonical_key"
        ).fetchall() == [
            ("213", EXPECTED_DEFINITION),
            ("existing", "concurrent WAL update survives"),
        ]


@pytest.mark.parametrize("replacement_kind", ["source-hardlink", "other-database"])
def test_destination_replacement_after_transaction_binding_is_not_modified(
    tmp_path, monkeypatch, replacement_kind
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    original_db = tmp_path / "original-memory.db"
    replacement_db = tmp_path / "replacement.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    source_before = _source_snapshot(source_db)
    store = UserMemoryStore(memory_db)
    original_id = store.remember(
        owner_key_for("existing-user"),
        NewUserMemory(
            memory_type=MemoryType.PROFILE,
            canonical_key="existing",
            display_name="Existing",
            content="existing destination content",
            aliases=("existing",),
        ),
    ).memory.id
    replacement_id: str | None = None
    if replacement_kind == "other-database":
        replacement_id = (
            UserMemoryStore(replacement_db)
            .remember(
                owner_key_for("replacement-user"),
                NewUserMemory(
                    memory_type=MemoryType.PROFILE,
                    canonical_key="replacement",
                    display_name="Replacement",
                    content="replacement must remain untouched",
                    aliases=("replacement",),
                ),
            )
            .memory.id
        )

    real_reconcile = UserMemoryStore.reconcile_exact_in_transaction

    def replace_then_reconcile(cls, connection, owner_key, memory):
        os.replace(memory_db, original_db)
        if replacement_kind == "source-hardlink":
            os.link(source_db, memory_db)
        else:
            os.replace(replacement_db, memory_db)
        return real_reconcile(connection, owner_key, memory)

    monkeypatch.setattr(
        UserMemoryStore,
        "reconcile_exact_in_transaction",
        classmethod(replace_then_reconcile),
    )

    with pytest.raises(
        migration_module.MigrationError, match="changed|distinct|conflict"
    ):
        migration_module.run(_run_args(source_db, memory_db))

    assert _source_snapshot(source_db) == source_before
    with closing(sqlite3.connect(original_db)) as connection, connection:
        assert connection.execute(
            "SELECT id FROM user_memories ORDER BY id"
        ).fetchall() == [(original_id,)]
        assert connection.execute(
            "SELECT COUNT(*) FROM user_memories WHERE canonical_key = '213'"
        ).fetchone() == (0,)
    if replacement_kind == "other-database":
        with closing(sqlite3.connect(memory_db)) as connection, connection:
            assert connection.execute(
                "SELECT id FROM user_memories ORDER BY id"
            ).fetchall() == [(replacement_id,)]
    else:
        with closing(sqlite3.connect(source_db)) as connection, connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = 'user_memories'"
            ).fetchone() == (0,)


def test_existing_destination_source_change_after_commit_is_not_reported_success(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    UserMemoryStore(memory_db)
    real_require_identity = migration_module._require_main_identity
    identity_checks = 0

    def require_then_change_source(destination_path, expected, source_path):
        nonlocal identity_checks
        real_require_identity(destination_path, expected, source_path)
        identity_checks += 1
        if identity_checks == 5:
            with closing(sqlite3.connect(source_db)) as connection, connection:
                connection.execute(
                    "INSERT INTO training_sessions (note) VALUES (?)",
                    ("source changed after destination commit",),
                )

    monkeypatch.setattr(
        migration_module, "_require_main_identity", require_then_change_source
    )

    with pytest.raises(migration_module.MigrationError, match="source.*changed"):
        migration_module.run(_run_args(source_db, memory_db))

    with closing(sqlite3.connect(source_db)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM training_sessions"
        ).fetchone() == (2,)
    with closing(sqlite3.connect(memory_db)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM user_memories WHERE canonical_key = '213'"
        ).fetchone() == (1,)


def test_missing_destination_source_change_at_link_is_not_reported_success(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    real_install = migration_module._install_new_destination

    def change_source_then_install(anchor, staging):
        with closing(sqlite3.connect(source_db)) as connection, connection:
            connection.execute(
                "INSERT INTO training_sessions (note) VALUES (?)",
                ("source changed at destination link",),
            )
        real_install(anchor, staging)

    monkeypatch.setattr(
        migration_module, "_install_new_destination", change_source_then_install
    )

    with pytest.raises(migration_module.MigrationError, match="source.*changed"):
        migration_module.run(_run_args(source_db, memory_db))

    with closing(sqlite3.connect(source_db)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM training_sessions"
        ).fetchone() == (2,)
    assert not memory_db.exists()
    assert not list(tmp_path.glob(".memory.db.*"))


def test_staging_descriptor_close_failure_cleans_temporary_database(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    real_close = migration_module.os.close

    def close_then_fail(descriptor):
        real_close(descriptor)
        raise OSError("private close failure")

    monkeypatch.setattr(migration_module.os, "close", close_then_fail)

    with pytest.raises(migration_module.MigrationError) as error:
        migration_module.run(_run_args(source_db, memory_db))

    assert "private close failure" not in str(error.value)
    assert not memory_db.exists()
    assert not list(tmp_path.glob(".memory.db.*"))


def test_stale_reconcile_is_reported_as_safe_migration_error(tmp_path, monkeypatch):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])

    def raise_stale(*_args):
        raise StaleMemoryError("private stale detail")

    monkeypatch.setattr(migration_module, "_apply_memory", raise_stale)

    with pytest.raises(migration_module.MigrationError, match="conflict") as error:
        migration_module.run(_run_args(source_db, memory_db))

    assert "private stale detail" not in str(error.value)
    assert not memory_db.exists()


def test_blob_note_is_counted_unrecognized_without_traceback_or_leak(tmp_path):
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    private_blob = "记住私人二进制内容"
    _create_source(source_db, [sqlite3.Binary(private_blob.encode())])

    result = _run_migration(source_db, memory_db, apply=True)

    assert result.returncode == 0, result.stderr
    assert "recognized=0" in result.stdout
    assert "unrecognized=1" in result.stdout
    assert "Traceback" not in result.stderr
    assert private_blob not in result.stdout + result.stderr
    assert not memory_db.exists()


def test_expanduser_failure_is_safe_and_does_not_leak_path(tmp_path):
    private_path = Path("~chatfit-user-that-does-not-exist/private-source.db")
    memory_db = tmp_path / "memory.db"

    result = _run_migration(private_path, memory_db, apply=True)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert os.fspath(private_path) not in result.stderr
    assert "path" in result.stderr.lower()
    assert not memory_db.exists()


def test_invalid_source_paths_and_non_sqlite_files_do_not_create_destination(tmp_path):
    missing = tmp_path / "missing.db"
    source_dir = tmp_path / "source-dir"
    source_dir.mkdir()
    invalid_source = tmp_path / "not-sqlite.db"
    invalid_source.write_text("private source data", encoding="utf-8")

    for source in (missing, source_dir, invalid_source):
        memory_db = tmp_path / f"memory-{source.name}.db"
        before = source.read_bytes() if source.is_file() else None
        result = _run_migration(source, memory_db, apply=True)
        assert result.returncode != 0
        assert "source database" in result.stderr.lower()
        assert not memory_db.exists()
        if before is not None:
            assert source.read_bytes() == before


def test_same_file_and_invalid_destination_fail_without_damaging_files(tmp_path):
    source_db = tmp_path / "source.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    source_before = _source_snapshot(source_db)

    same_file_result = _run_migration(source_db, source_db, apply=True)

    assert same_file_result.returncode != 0
    assert "distinct" in same_file_result.stderr.lower()
    assert _source_snapshot(source_db) == source_before

    destination_dir = tmp_path / "destination-dir"
    destination_dir.mkdir()
    directory_result = _run_migration(source_db, destination_dir, apply=True)
    assert directory_result.returncode != 0
    assert "destination database" in directory_result.stderr.lower()
    assert _source_snapshot(source_db) == source_before

    invalid_destination = tmp_path / "invalid-memory.db"
    invalid_destination.write_bytes(b"existing destination bytes")
    destination_before = invalid_destination.read_bytes()
    invalid_result = _run_migration(source_db, invalid_destination, apply=True)
    assert invalid_result.returncode != 0
    assert "destination database" in invalid_result.stderr.lower()
    assert invalid_destination.read_bytes() == destination_before
    assert _source_snapshot(source_db) == source_before
