import sqlite3
import pytest
from datetime import datetime, timedelta

from agents.training_recorder import make_record_training_graph
from utils.llm_factory import LLMConfig
from utils.db import init_db

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
def test_make_record_training_graph(llm_config, temp_db_path):
    message = HumanMessage(content="I ran 5km in 30 minutes yesterday. RPE was around 5. Felt great!")
    initial_state = {
        "messages": [message]
    }
    app = make_record_training_graph(llm_config, temp_db_path)
    app.invoke(initial_state)

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
        assert saved_session["date"] == (datetime.now() - timedelta(days=1)).date().isoformat()

@pytest.mark.e2e
def test_record_training_with_double_sessions(llm_config, temp_db_path):
    initial_state = {
        "messages": ["I ran 10km in 30 minutes yesterday, then I snatched a 24kg kettlebell 150 times in 15 minutes. RPE was 10. Felt tired"]
    }
    app = make_record_training_graph(llm_config, temp_db_path)
    app.invoke(initial_state)

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

        assert len(rows) == 2

        kb_session = next((row for row in rows if "kettlebell" in row["practice_name"].lower()), None)
        assert kb_session is not None
        assert kb_session["duration"] == 15
        assert kb_session["rpe"] == 10

@pytest.mark.e2e
def test_retrieve_training(llm_config, temp_db_path):
    # data preparation
    message = HumanMessage(content="I swung the 32KG kettlebell for 200 times today!")
    state = {"messages": [message]}
    app = make_record_training_graph(llm_config, temp_db_path)
    app.invoke(state)

    new_message = HumanMessage(content="What training did I do today?")
    new_state = {"messages": [new_message]}
    app = make_record_training_graph(llm_config, temp_db_path)
    response = app.invoke({"messages": new_state["messages"]})
    final_text = response["messages"][-1].content[0]["text"]

    assert "200" in final_text
    assert "kettlebell" in final_text.lower()
    assert "32kg" in final_text.lower()

    
