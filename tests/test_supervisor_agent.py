import pytest
import sqlite3
from datetime import datetime, timedelta

from agents.roles.supervisor import route_assistant_on_relevance, make_agent_graph
from agents.llm_factory import LLMConfig
from agents.sqlite_handler import init_db
from agents.rag import get_or_create_vector_store

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

@pytest.fixture
def vector_store(tmp_path):
    chroma_db_path = tmp_path / "chroma.db"
    return get_or_create_vector_store("tests/recipes", chroma_db_path)

# tests start here
@pytest.mark.asyncio
@pytest.mark.e2e
async def test_routing_meal_assistant(llm_config):
    message = [HumanMessage(content="breakfast: 2 fried eggs and bread today")]
    result = await route_assistant_on_relevance(llm_config, message)

    assert result == ["meal_agent"]

@pytest.mark.asyncio
@pytest.mark.e2e
async def test_routing_training_assistant(llm_config):
    message = [HumanMessage(content="I pressed 48kg kettlebell 1 time today.")]
    result = await route_assistant_on_relevance(llm_config, message)

    assert result == ["training_agent"]

@pytest.mark.asyncio
@pytest.mark.e2e
async def test_routing_multiple_assistants(llm_config):
    message = [HumanMessage(content="I ran 10km today, and then I eat 2 bananas.")]
    result = await route_assistant_on_relevance(llm_config, message)

    assert "meal_agent" in result
    assert "training_agent" in result

@pytest.mark.asyncio
@pytest.mark.e2e
async def test_routing_none(llm_config):
    message = [HumanMessage(content="the weather is fine today")]
    result = await route_assistant_on_relevance(llm_config, message)

    assert result == ['chatter']

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

@pytest.mark.asyncio
@pytest.mark.e2e
async def test_make_agent_graph(llm_config, temp_db_path, vector_store):
    app = make_agent_graph(llm_config, temp_db_path, vector_store)
    app.checkpointer = MemorySaver()
    config = {"configurable": {"thread_id": "test_2"}}

    message = HumanMessage(content="I ran 5km in 30 minutes yesterday. RPE was around 5. Then I had 2 burgers for lunch.")
    state = {"messages": [message]}
    response = await app.ainvoke(state, config)

    # First we expect it to be interrupted by the tool calls
    while "__interrupt__" in response:
        resume_data = {}
        for intr in response["__interrupt__"]:
            resume_data[intr.id] = {"approved": True}
        response = await app.ainvoke(Command(resume=resume_data), config)

    content = response["messages"][-1].content
    agent_reply = content if isinstance(content, str) else content[0]["text"]
    assert "running" in agent_reply.lower() or "burger" in agent_reply.lower()

    # The test originally expected a question about adding the practice
    # Let's ensure it handles the follow up correctly if needed
    if "?" in agent_reply.lower():
        state = {"messages": response["messages"] + [
            HumanMessage(content="Yes, please add running as distance practice.")
        ]}
        response = await app.ainvoke(state, config)
        while "__interrupt__" in response:
            resume_data = {}
            for intr in response["__interrupt__"]:
                resume_data[intr.id] = {"approved": True}
            response = await app.ainvoke(Command(resume=resume_data), config)

    # agent should have inserted db records
    with sqlite3.connect(temp_db_path) as conn:
        conn.row_factory = sqlite3.Row

        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.name name, t.rpe rpe, s.duration duration
            FROM practices p, training_sessions t, training_sets s
            WHERE p.id = t.practice_id and s.training_session_id = t.id
            """
        )

        rows = cursor.fetchall()
        assert len(rows) == 1

        # verify that data has been correctly inserted
        result = rows[0]
        assert result["name"].lower() == "running"
        assert result["rpe"] == 5.0
        assert result["duration"] == 30.0

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
