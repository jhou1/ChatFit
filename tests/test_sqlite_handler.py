from datetime import datetime, timedelta
import sqlite3

from agents.models import TrainingInputRecorder, TrainingSet, TrainingSession
from agents.sqlite_handler import (
    add_training_session,
    get_training_sessions_of_last_n_days,
    init_db,
)


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
