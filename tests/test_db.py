import sqlite3
from datetime import datetime

from schema.training import TrainingLog
from storage.db import init_db, add_training_log


def test_add_training_log(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    training_log = TrainingLog(
        date=datetime.now().date().isoformat(),
        practice_name="test",
        note="feel good practice."
    )
    row_id = add_training_log(training_log, db_path)
    assert isinstance(row_id, int)
