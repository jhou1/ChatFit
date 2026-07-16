import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from agents.roles.supervisor import make_agent_graph
from agents.llm_factory import LLMConfig
from agents.sqlite_handler import init_db


@pytest.fixture
def temp_db_path(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


@pytest.mark.asyncio
@pytest.mark.e2e
@patch("agents.roles.supervisor._execute_llm_query_safely", new_callable=AsyncMock)
@patch("agents.roles.supervisor.route_assistant_on_relevance", new_callable=AsyncMock)
async def test_context_governance_truncates_messages(
    mock_route, mock_execute, temp_db_path
):
    # Setup mock returns
    mock_route.return_value = ["chatter"]

    mock_execute.return_value = {
        "messages": AIMessage(content="This is a new summary.")
    }

    llm_config = LLMConfig(provider="google", model_name="gemini-3.5-flash", kwargs={})
    app = make_agent_graph(llm_config, str(temp_db_path), None)

    messages = []
    for i in range(25):
        msg = HumanMessage(content=f"msg {i}")
        msg.id = f"id_{i}"
        messages.append(msg)

    state = {"messages": messages}

    # We invoke the graph. It should run context_governance -> assistant_selector -> chatter
    response = await app.ainvoke(state)

    # Check that the summary was updated
    # Wait, ainvoke returns the final state! So response is the AgentState
    assert "summary" in response
    assert response["summary"] == "This is a new summary."

    # The final messages should have removed the first 10 messages.
    # So 25 - 10 = 15 messages remaining + 1 chatter response = 16 messages.
    assert len(response["messages"]) == 16
    assert response["messages"][0].id == "id_10"

    # Verify the execute call for summarization was made with correct prompt
    # The first call to execute is from context_governance, the second from chatter
    assert mock_execute.call_count == 2
    prompt_sent = mock_execute.call_args_list[0][0][1][0].content
    assert "msg 0" in prompt_sent
    assert "msg 9" in prompt_sent
    assert "msg 10" not in prompt_sent


@pytest.mark.asyncio
@pytest.mark.e2e
@patch("agents.roles.supervisor._execute_llm_query_safely", new_callable=AsyncMock)
@patch("agents.roles.supervisor.route_assistant_on_relevance", new_callable=AsyncMock)
async def test_context_governance_preserves_tool_calls(
    mock_route, mock_execute, temp_db_path
):
    mock_route.return_value = ["chatter"]
    mock_execute.return_value = {"messages": AIMessage(content="Summary with tool.")}

    llm_config = LLMConfig(provider="google", model_name="gemini-3.5-flash", kwargs={})
    app = make_agent_graph(llm_config, str(temp_db_path), None)

    messages = []
    for i in range(10):
        msg = HumanMessage(content=f"msg {i}")
        msg.id = f"id_{i}"
        messages.append(msg)

    # Index 10: AIMessage with tool call
    ai_msg = AIMessage(content="")
    ai_msg.id = "id_10"
    ai_msg.tool_calls = [{"name": "some_tool"}]
    messages.append(ai_msg)

    # Index 11: ToolMessage
    tool_msg = ToolMessage(content="success", tool_call_id="call_123")
    tool_msg.id = "id_11"
    messages.append(tool_msg)

    # Fill the rest to exceed 20
    for i in range(12, 25):
        msg = HumanMessage(content=f"msg {i}")
        msg.id = f"id_{i}"
        messages.append(msg)

    state = {"messages": messages}

    response = await app.ainvoke(state)

    assert "summary" in response
    assert response["summary"] == "Summary with tool."

    # Cutoff should have shifted to include index 10 and 11
    # Total removed should be 12. Originally 25 messages, 25 - 12 = 13 + 1 (chatter) = 14 messages
    assert len(response["messages"]) == 14
    assert response["messages"][0].id == "id_12"
