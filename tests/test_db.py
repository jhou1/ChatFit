import sqlite3
from datetime import datetime

from schema.meal import MealRecord
from schema.training import TrainingSession
from storage.db import init_db, add_training_session, add_meal_record


def test_add_training_session(tmp_path):
    db_path = tmp_path / "training_session_test.db"
    init_db(db_path)

    training_session = TrainingSession(
        date=datetime.now().date().isoformat(),
        practice_name="test",
        note="feel good practice."
    )
    row_id = add_training_session(training_session, db_path)
    assert isinstance(row_id, int)

def test_add_meal_record(tmp_path):
    db_path = tmp_path / "meal_record_test.db"
    init_db(db_path)
    meal_record = MealRecord(
        date=datetime.now().date().isoformat(),
        note="breakfast: milk and egg, dinner: fish and rice"
    )
    row_id = add_meal_record(meal_record, db_path)
    assert isinstance(row_id, int)
