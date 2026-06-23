import pytest
import sqlite3
from datetime import datetime, timedelta

from agents.assistant_selector import route_assistant_on_relevance, make_agent_graph
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
def test_routing_meal_assistant(llm_config):
    message = "breakfast: 2 fried eggs and bread today"
    result = route_assistant_on_relevance(llm_config, message)

    assert result == ["meal_agent"]

@pytest.mark.e2e
def test_routing_training_assistant(llm_config):
    message = "I pressed 48kg kettlebell 1 time today."
    result = route_assistant_on_relevance(llm_config, message)

    assert result == ["training_agent"]

@pytest.mark.e2e
def test_routing_multiple_assistants(llm_config):
    message = "I ran 10km today, and then I eat 2 bananas."
    result = route_assistant_on_relevance(llm_config, message)

    assert "meal_agent" in result
    assert "training_agent" in result

@pytest.mark.e2e
def test_routing_none(llm_config):
    message = "the weather is fine today"
    result = route_assistant_on_relevance(llm_config, message)

    assert result == []

@pytest.mark.e2e
def test_make_agent_graph(llm_config: LLMConfig, temp_db_path: str):
    app = make_agent_graph(llm_config, temp_db_path)

    message = HumanMessage(content="I ran 5km in 30 minutes yesterday. RPE was around 5. Then I had 2 burgers for lunch.")
    initial_state = {
        "messages": [message]
    }
    response = app.invoke(initial_state)

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
