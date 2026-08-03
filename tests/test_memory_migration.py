import argparse
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
    with sqlite3.connect(path) as connection:
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
    with sqlite3.connect(path) as connection:
        schema = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        rows = connection.execute(
            "SELECT id, note FROM training_sessions ORDER BY id"
        ).fetchall()
    return file_bytes, schema, rows


def _memory_rows(path: Path) -> tuple[list[tuple], list[tuple]]:
    with sqlite3.connect(path) as connection:
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


@pytest.mark.parametrize("negative_marker", ["不要记住", "无需记忆", "别记住"])
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
    note = f"2-1-3 是一个训练模板（请记住），它代表{partial_definition}"
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
        "2 - 1 - 3 是一个训练模板（请记住），它代表：2个抓举、1个挺举、"
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


def test_apply_updates_different_existing_content_with_stable_id(tmp_path):
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

    result = _run_migration(source_db, memory_db, apply=True)
    memories, aliases = _memory_rows(memory_db)

    assert result.returncode == 0, result.stderr
    assert "status=updated" in result.stdout
    assert len(memories) == 1
    assert memories[0] == (
        original.id,
        owner_key_for("user-a"),
        "training_template",
        "213",
        "2-1-3",
        EXPECTED_DEFINITION,
        2,
    )
    assert {alias[1] for alias in aliases} == {"213", "2-1-3", "壶铃213"}


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
    assert memories[0][4:] == ("2-1-3", EXPECTED_DEFINITION, 2)
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

    with pytest.raises(
        migration_module.MigrationError, match="changed|distinct|conflict"
    ):
        migration_module.run(_run_args(source_db, memory_db))

    assert _source_snapshot(source_db) == before
    with sqlite3.connect(source_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'user_memories'"
        ).fetchone() == (0,)
    assert not list(tmp_path.glob(".memory.db.*"))


def test_concurrent_destination_creation_is_preserved_and_staging_is_rejected(
    tmp_path, monkeypatch
) -> None:
    source_db = tmp_path / "source.db"
    memory_db = tmp_path / "memory.db"
    _create_source(source_db, [HISTORICAL_NOTE])
    real_apply = migration_module._apply_memory
    concurrent_id: str | None = None

    def apply_then_create_destination(staging_path, user_id, definition):
        nonlocal concurrent_id
        status = real_apply(staging_path, user_id, definition)
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
    real_require = migration_module._require_unchanged
    destination_checks = 0
    concurrent_id: str | None = None

    def require_then_create(path, expected, *, role):
        nonlocal concurrent_id, destination_checks
        real_require(path, expected, role=role)
        if role == "destination" and path == memory_db:
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

    monkeypatch.setattr(migration_module, "_require_unchanged", require_then_create)

    with pytest.raises(migration_module.MigrationError, match="changed|conflict"):
        migration_module.run(_run_args(source_db, memory_db))

    with sqlite3.connect(memory_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM user_memories WHERE id = ?", (concurrent_id,)
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM user_memories WHERE canonical_key = '213'"
        ).fetchone() == (0,)
    assert not list(tmp_path.glob(".memory.db.*"))


def test_destination_updated_after_final_snapshot_is_serially_reconciled(
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
    real_require = migration_module._require_unchanged
    destination_checks = 0
    concurrent_id: str | None = None

    def require_then_update(path, expected, *, role):
        nonlocal concurrent_id, destination_checks
        real_require(path, expected, role=role)
        if role == "destination" and path == memory_db:
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

    monkeypatch.setattr(migration_module, "_require_unchanged", require_then_update)

    assert migration_module.run(_run_args(source_db, memory_db)) == 0

    with sqlite3.connect(memory_db) as connection:
        stored_ids = {
            row[0]
            for row in connection.execute("SELECT id FROM user_memories ORDER BY id")
        }
        assert concurrent_id is not None
        assert {original_id, concurrent_id} < stored_ids
        assert connection.execute(
            "SELECT COUNT(*) FROM user_memories WHERE canonical_key = '213'"
        ).fetchone() == (1,)
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
    with sqlite3.connect(memory_db) as connection:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    Path(f"{memory_db}-wal").unlink(missing_ok=True)
    Path(f"{memory_db}-shm").unlink(missing_ok=True)
    assert not Path(f"{memory_db}-wal").exists()
    assert not Path(f"{memory_db}-shm").exists()

    real_require = migration_module._require_unchanged
    destination_checks = 0
    writer: sqlite3.Connection | None = None

    def require_then_write_wal(path, expected, *, role):
        nonlocal destination_checks, writer
        real_require(path, expected, role=role)
        if role == "destination" and path == memory_db:
            destination_checks += 1
            if destination_checks == 1:
                writer = sqlite3.connect(memory_db)
                writer.execute(
                    "UPDATE user_memories SET content = ? WHERE id = ?",
                    ("concurrent WAL update survives", original_id),
                )
                writer.commit()
                assert Path(f"{memory_db}-wal").stat().st_size > 0

    monkeypatch.setattr(migration_module, "_require_unchanged", require_then_write_wal)

    try:
        assert migration_module.run(_run_args(source_db, memory_db)) == 0
    finally:
        if writer is not None:
            writer.close()

    with sqlite3.connect(memory_db) as connection:
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
    with sqlite3.connect(original_db) as connection:
        assert connection.execute(
            "SELECT id FROM user_memories ORDER BY id"
        ).fetchall() == [(original_id,)]
        assert connection.execute(
            "SELECT COUNT(*) FROM user_memories WHERE canonical_key = '213'"
        ).fetchone() == (0,)
    if replacement_kind == "other-database":
        with sqlite3.connect(memory_db) as connection:
            assert connection.execute(
                "SELECT id FROM user_memories ORDER BY id"
            ).fetchall() == [(replacement_id,)]
    else:
        with sqlite3.connect(source_db) as connection:
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
        if identity_checks == 3:
            with sqlite3.connect(source_db) as connection:
                connection.execute(
                    "INSERT INTO training_sessions (note) VALUES (?)",
                    ("source changed after destination commit",),
                )

    monkeypatch.setattr(
        migration_module, "_require_main_identity", require_then_change_source
    )

    with pytest.raises(migration_module.MigrationError, match="source.*changed"):
        migration_module.run(_run_args(source_db, memory_db))

    with sqlite3.connect(source_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM training_sessions"
        ).fetchone() == (2,)
    with sqlite3.connect(memory_db) as connection:
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

    def change_source_then_install(staging_path, destination_path):
        with sqlite3.connect(source_db) as connection:
            connection.execute(
                "INSERT INTO training_sessions (note) VALUES (?)",
                ("source changed at destination link",),
            )
        real_install(staging_path, destination_path)

    monkeypatch.setattr(
        migration_module, "_install_new_destination", change_source_then_install
    )

    with pytest.raises(migration_module.MigrationError, match="source.*changed"):
        migration_module.run(_run_args(source_db, memory_db))

    with sqlite3.connect(source_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM training_sessions"
        ).fetchone() == (2,)
    with sqlite3.connect(memory_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM user_memories WHERE canonical_key = '213'"
        ).fetchone() == (1,)
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
