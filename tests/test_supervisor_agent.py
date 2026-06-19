import sqlite3
import pytest

from nodes.supervisor_agent import make_supervisor_agent
from llm_factory.llm_factory import LLMConfig
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
        # required config in my test env
        kwargs={"client_args": {"proxy": "socks5://127.0.0.1:8990"}},
        temperature=0
    )

@pytest.mark.e2e
def test_make_supervisor_agent(llm_config, temp_db_path):
    app = make_supervisor_agent(llm_config, temp_db_path)

    user_input = HumanMessage(content="I ran 5km in 30 minutes yesterday. RPE was around 5. Then I had 2 burgers for lunch.")
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
            SELECT * from training_sessions
            """
        )
        rows = cursor.fetchall()

        assert len(rows) == 1

        # verify that data has been correctly inserted
        saved_session = rows[0]
        assert saved_session["practice_name"].lower() == "running"
        assert saved_session["rpe"] == 5
        assert saved_session["duration"] == 30

    with sqlite3.connect(temp_db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * from meal_records
            """
        )
        rows = cursor.fetchall()

        assert len(rows) == 1

        # verify that data has been correctly inserted
        saved_record = rows[0]
        assert saved_record["meal_type"] == "lunch"
        assert "burger" in saved_record["items"].lower()
