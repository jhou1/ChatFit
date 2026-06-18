import sqlite3
import pytest
from datetime import datetime, timedelta

from nodes.training_agent import make_training_subgraph
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
        temperature=0
    )

@pytest.mark.e2e
def test_make_training_subgraph(llm_config, temp_db_path):
    app = make_training_subgraph(llm_config, temp_db_path)

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
def test_make_training_subgraph_with_double_sessions(llm_config, temp_db_path):
    app = make_training_subgraph(llm_config, temp_db_path)

    # intentionally specify 2 training sessions in user input
    user_input = HumanMessage(content="I ran 10km in 30 minutes yesterday, then I snatched a 24kg kettlebell 150 times in 15 minutes. RPE was 10. Felt tired")
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

        assert len(rows) == 2

        kb_session = next((row for row in rows if "kettlebell" in row["practice_name"].lower()), None)
        assert kb_session is not None
        assert kb_session["duration"] == 15
        assert kb_session["rpe"] == 10
