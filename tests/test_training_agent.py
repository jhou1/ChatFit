import sqlite3
import pytest
from datetime import datetime, timedelta

from nodes.training_agent import make_training_log_subgraph
from llm_factory.llm_factory import LLMConfig, create_chat_model
from storage.db import init_db

from langchain_core.messages import HumanMessage

@pytest.fixture
def temp_db_path(tmp_path):
    db_path = tmp_path / "test_training_agent.db"
    init_db(db_path)
    return db_path

@pytest.fixture
def llm_config():
    return LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        temperature=0
    )

@pytest.mark.e2e
def test_make_training_log_subgraph(llm_config, temp_db_path):
    app = make_training_log_subgraph(llm_config, temp_db_path)

    user_input = HumanMessage(content="I ran 5km in 30 minutes yesterday. RPE was around 5. Felt great!")
    response = app.invoke({"messages": [user_input]})

    messages = response["messages"]

    # agent should have responded
    assert len(messages) > 1

    # agent should have inserted db records
    with sqlite3.connect(temp_db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * from training_log
            """
        )
        rows = cursor.fetchall()

        assert len(rows) == 1

        # verify that data has been correctly inserted
        saved_training_log = rows[0]
        assert saved_training_log["practice_name"].lower() == "running"
        assert saved_training_log["rpe"] == 5
        assert saved_training_log["duration"] == 30
        assert saved_training_log["date"] == (datetime.now() - timedelta(days=1)).date().isoformat()
