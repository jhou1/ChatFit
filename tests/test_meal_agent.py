import sqlite3
import pytest
from datetime import datetime, timedelta

from nodes.meal_agent import make_meal_subgraph
from llm_factory.llm_factory import LLMConfig
from storage.db import init_db

from langchain_core.messages import HumanMessage

@pytest.fixture
def temp_db_path(tmp_path):
    db_path = tmp_path / "test_meal_agent.db"
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
def test_make_meal_subgraph(llm_config, temp_db_path):
    app = make_meal_subgraph(llm_config, temp_db_path)

    user_input = HumanMessage(content="breakfast: 2 fried eggs and bread today")
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
            SELECT * from meal_records
            """
        )
        rows = cursor.fetchall()

        assert len(rows) == 1

        # verify that data has been correctly inserted
        saved_record = rows[0]
        assert saved_record["date"] == datetime.now().date().isoformat()
        assert saved_record["meal_type"] == "breakfast"
        assert "eggs" in saved_record["items"]
        assert "bread" in saved_record["items"]


