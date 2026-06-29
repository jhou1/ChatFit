from models import TrainingSessionInfo
from utils.db import add_training_session, get_training_sessions_of_last_n_days, init_db
from datetime import datetime, timedelta

def test_add_training_session(tmp_path):
    db_path = tmp_path / "training_session_test.db"
    init_db(db_path)

    training_session = TrainingSessionInfo(
        date=datetime.now().date().isoformat(),
        practice_name="test",
        note="feel good practice."
    )
    row_id = add_training_session(training_session, db_path)
    assert isinstance(row_id, int)


def test_get_training_sessiosn_of_last_n_days(tmp_path):
    db_path = tmp_path / "training_session_test.db"
    init_db(db_path)

    # prepare test data
    for i in range(1,8):
        ts = TrainingSessionInfo(
           date=(datetime.now().date() - timedelta(i)).isoformat(),
           practice_name=f"test{i}",
           note="feel good",
        )
        add_training_session(ts, db_path)

    training_sessions = get_training_sessions_of_last_n_days(7, db_path)
    assert len(training_sessions) == 7
