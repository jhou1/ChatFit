import sqlite3
import pytest
from datetime import datetime

from agents.roles.meal import make_meal_subagent_graph
from agents.llm_factory import LLMConfig
from agents.sqlite_handler import init_db
from agents.rag import get_or_create_vector_store

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

@pytest.fixture
def vector_store(tmp_path):
    chroma_db_path = tmp_path / "chroma.db"
    return get_or_create_vector_store("tests/recipes", chroma_db_path)


from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

@pytest.mark.asyncio
@pytest.mark.e2e
async def test_make_meal_subgraph(llm_config, temp_db_path, vector_store):
    message = HumanMessage(content="breakfast: 2 fried eggs and bread today")
    initial_state = {
        "messages": [message]
    }

    app = make_meal_subagent_graph(llm_config, temp_db_path, vector_store)
    app.checkpointer = MemorySaver()
    config = {"configurable": {"thread_id": "test_1"}}
    
    # We may need multiple resumes if the LLM makes a mistake and retries
    state = await app.ainvoke(initial_state, config)
    
    while "__interrupt__" in state:
        state = await app.ainvoke(Command(resume={"approved": True}), config)

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
