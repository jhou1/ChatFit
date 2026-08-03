import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from agents.memory.models import MemoryType, NewUserMemory
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


def _create_source(path: Path, notes: list[str]) -> None:
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
    conflicting = HISTORICAL_NOTE.replace("10个波比跳", "12个波比跳")
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
    assert _memory_rows(memory_db) == before
    assert before[0][0][0] == existing.id


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
