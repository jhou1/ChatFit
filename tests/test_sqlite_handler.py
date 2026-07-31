from datetime import date, datetime, timedelta
import sqlite3

import agents.sqlite_handler as sqlite_handler
from agents.models import MealInfo, TrainingInputRecorder, TrainingSet, TrainingSession
from agents.sqlite_handler import (
    add_meal_log,
    add_training_session,
    get_training_sessions_of_last_n_days,
    init_db,
)


def seed_training_and_meal(db_path, target_date: date, label: str) -> None:
    add_training_session(
        TrainingInputRecorder(
            date=target_date,
            sessions=[
                TrainingSession(
                    practice_name=f"Squat {label}",
                    practice_type="weighted",
                    rpe=7,
                    note=label,
                    sets=[TrainingSet(set_number=1, weight=100, reps=5)],
                )
            ],
            confirm_new_practices=True,
        ),
        str(db_path),
    )
    add_meal_log(
        MealInfo(
            date=target_date,
            meal_type="dinner",
            items=label,
            note=label,
        ),
        str(db_path),
    )


def test_explicit_date_reads_do_not_use_sqlite_now(tmp_path):
    """Breaks if a requested calendar date is ignored and all rows are returned."""
    temp_db_path = tmp_path / "explicit_date.db"
    init_db(temp_db_path)
    add_meal_log(
        MealInfo(
            date=date(2026, 8, 1),
            meal_type="dinner",
            items="rice and fish",
            note="post-training",
        ),
        str(temp_db_path),
    )
    add_training_session(
        TrainingInputRecorder(
            date=date(2026, 8, 1),
            sessions=[
                TrainingSession(
                    practice_name="Squat",
                    practice_type="weighted",
                    rpe=7,
                    note="comfortable",
                    sets=[TrainingSet(set_number=1, weight=100, reps=5)],
                )
            ],
            confirm_new_practices=True,
        ),
        str(temp_db_path),
    )
    seed_training_and_meal(temp_db_path, date(2026, 7, 25), "not-target")

    meals = sqlite_handler.get_meal_records_for_date(
        date(2026, 8, 1), str(temp_db_path)
    )
    training = sqlite_handler.get_training_records_for_date(
        date(2026, 8, 1), str(temp_db_path)
    )

    assert [row["items"] for row in meals] == ["rice and fish"]
    assert training == [
        {
            "training_date": "2026-08-01",
            "practice_name": "Squat",
            "rpe": 7,
            "note": "comfortable",
            "total_sets": 1,
        }
    ]


def test_inclusive_week_range_is_exactly_sunday_through_saturday(tmp_path):
    """Breaks if explicit review ranges exclude either endpoint or include outside data."""
    temp_db_path = tmp_path / "explicit_range.db"
    init_db(temp_db_path)
    seed_training_and_meal(temp_db_path, date(2026, 7, 25), "outside")
    seed_training_and_meal(temp_db_path, date(2026, 7, 26), "sunday")
    seed_training_and_meal(temp_db_path, date(2026, 8, 1), "saturday")

    training = sqlite_handler.get_aggregated_training_between(
        date(2026, 7, 26), date(2026, 8, 1), str(temp_db_path)
    )
    meals = sqlite_handler.get_meal_records_between(
        date(2026, 7, 26), date(2026, 8, 1), str(temp_db_path)
    )

    assert {row["training_date"] for row in training} == {
        "2026-07-26",
        "2026-08-01",
    }
    assert {row["date"] for row in meals} == {"2026-07-26", "2026-08-01"}


def test_add_training_session(tmp_path):
    db_path = tmp_path / "training_session_test.db"
    init_db(db_path)

    test_input = TrainingInputRecorder(
        date=datetime.now().date(),
        sessions=[
            TrainingSession(
                practice_name="Squat",
                practice_type="weighted",
                note="Testing",
                sets=[TrainingSet(set_number=1, weight=100, reps=10)],
            )
        ],
        confirm_new_practices=True,
    )

    result = add_training_session(test_input, db_path)
    assert result == "Training log saved successfully!"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        cursor = conn.cursor()
        cursor.execute("SELECT * from training_sessions")
        rows = cursor.fetchall()
        assert len(rows) == 1

        cursor.execute("""
            SELECT p.name, p.type from training_sessions t, practices p
            WHERE t.practice_id = p.id
            AND t.note == "Testing"
            """)
        practice = cursor.fetchall()[0]
        assert practice["name"] == "Squat"
        assert practice["type"].lower() == "weighted"


def test_add_training_session_is_idempotent_with_operation_id(tmp_path):
    db_path = tmp_path / "training_session_test.db"
    init_db(db_path)
    test_input = TrainingInputRecorder(
        operation_id="hitl:training-1",
        date=datetime.now().date(),
        sessions=[
            TrainingSession(
                practice_name="Squat",
                practice_type="weighted",
                note="Testing",
                sets=[TrainingSet(set_number=1, weight=100, reps=10)],
            )
        ],
        confirm_new_practices=True,
    )

    assert (
        add_training_session(test_input, db_path) == "Training log saved successfully!"
    )
    assert (
        add_training_session(test_input, db_path) == "Training log saved successfully!"
    )

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        assert (
            cursor.execute("SELECT COUNT(*) FROM training_sessions").fetchone()[0] == 1
        )
        assert cursor.execute("SELECT COUNT(*) FROM training_sets").fetchone()[0] == 1
        assert (
            cursor.execute("SELECT COUNT(*) FROM write_operations").fetchone()[0] == 1
        )


def test_add_training_session_creates_operation_ledger_for_pre_ledger_database(
    tmp_path,
):
    db_path = tmp_path / "pre_ledger_training_session_test.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE write_operations")

    test_input = TrainingInputRecorder(
        operation_id="hitl:pre-ledger-1",
        date=datetime.now().date(),
        sessions=[
            TrainingSession(
                practice_name="Squat",
                practice_type="weighted",
                note="Testing legacy database",
                sets=[TrainingSet(set_number=1, weight=100, reps=10)],
            )
        ],
        confirm_new_practices=True,
    )

    assert (
        add_training_session(test_input, db_path) == "Training log saved successfully!"
    )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT operation_id FROM write_operations").fetchone() == (
            "hitl:pre-ledger-1",
        )


def test_add_training_session_rolls_back_operation_marker_after_business_failure(
    tmp_path,
):
    db_path = tmp_path / "training_session_test.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TRIGGER fail_training_session_insert
            BEFORE INSERT ON training_sessions
            BEGIN
                SELECT RAISE(ABORT, 'forced mid-write business failure');
            END
            """)

    test_input = TrainingInputRecorder(
        operation_id="hitl:rollback-1",
        date=datetime.now().date(),
        sessions=[
            TrainingSession(
                practice_name="Squat",
                practice_type="weighted",
                note="This write must roll back",
                sets=[TrainingSet(set_number=1, weight=100, reps=10)],
            )
        ],
        confirm_new_practices=True,
    )

    result = add_training_session(test_input, db_path)

    assert "forced mid-write business failure" in result
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM write_operations").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM practices").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM training_sessions").fetchone() == (0,)


def test_add_training_session_without_operation_id_creates_each_session(tmp_path):
    db_path = tmp_path / "training_session_test.db"
    init_db(db_path)
    test_input = TrainingInputRecorder(
        date=datetime.now().date(),
        sessions=[
            TrainingSession(
                practice_name="Squat",
                practice_type="weighted",
                note="Testing",
                sets=[TrainingSet(set_number=1, weight=100, reps=10)],
            )
        ],
        confirm_new_practices=True,
    )

    assert (
        add_training_session(test_input, db_path) == "Training log saved successfully!"
    )
    assert (
        add_training_session(test_input, db_path) == "Training log saved successfully!"
    )

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        assert (
            cursor.execute("SELECT COUNT(*) FROM training_sessions").fetchone()[0] == 2
        )


def test_get_training_sessiosn_of_last_n_days(tmp_path):
    db_path = tmp_path / "training_session_test.db"
    init_db(db_path)

    # prepare test data
    for i in range(1, 8):
        test_input = TrainingInputRecorder(
            date=datetime.now().date() - timedelta(i),
            sessions=[
                TrainingSession(
                    practice_name=f"Test{i}",
                    practice_type="bodyweight",
                    note="Testing",
                    sets=[TrainingSet(set_number=1, weight=100, reps=10)],
                )
            ],
            confirm_new_practices=True,
        )

        add_training_session(test_input, db_path)

    training_sessions = get_training_sessions_of_last_n_days(7, db_path)
    assert len(training_sessions) == 7
