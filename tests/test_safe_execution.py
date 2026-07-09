import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch

from langchain_core.messages import ToolMessage, AIMessage

from tools.safe_execution import (
    _is_transient_tool_error,
    _is_rate_limit_tool_error,
    _execute_single_tool_safely,
    _execute_llm_query_safely,
    MAX_RETRIES,
    MAX_OUTPUT_TOKENS,
    TRUNCATE_WARNINGS,
    LLM_TIMEOUT_SECONDS
)

def test_is_transient_error():
    assert _is_transient_tool_error(TimeoutError())
    assert _is_transient_tool_error(asyncio.TimeoutError())
    assert _is_transient_tool_error(ConnectionError())
    assert _is_transient_tool_error(OSError())
    assert _is_transient_tool_error(Exception("The connection reset by peer"))
    assert _is_transient_tool_error(Exception("temporary failure in name resolution"))
    
    # Should not be transient
    assert not _is_transient_tool_error(ValueError("Invalid argument"))
    assert not _is_transient_tool_error(Exception("User denied access"))

def test_is_rate_limit_error():
    assert _is_rate_limit_tool_error(Exception("429 Too Many Requests"))
    assert _is_rate_limit_tool_error(Exception("rate limit exceeded"))
    assert _is_rate_limit_tool_error(Exception("Server returned 429"))
    
    # Should not be rate limit
    assert not _is_rate_limit_tool_error(Exception("Timeout"))
    assert not _is_rate_limit_tool_error(ValueError("400 Bad Request"))

@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_execute_single_tool_safely_success(mock_sleep):
    mock_tool = Mock()
    mock_tool.name = "test_tool"
    mock_tool.invoke.return_value = "Tool execution success"
    
    tool_call = {"name": "test_tool", "args": {"input": "test"}, "id": "call_1"}
    
    result = await _execute_single_tool_safely(tool_call, [mock_tool])
    
    assert isinstance(result, ToolMessage)
    assert result.content == "Tool execution success"
    assert result.tool_call_id == "call_1"
    assert result.status != "error"
    mock_tool.invoke.assert_called_once_with({"input": "test"})
    mock_sleep.assert_not_called()

@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_execute_single_tool_safely_transient_retry(mock_sleep):
    mock_tool = Mock()
    mock_tool.name = "test_tool"
    # Fail twice with ConnectionError, then succeed
    mock_tool.invoke.side_effect = [ConnectionError("temporary"), ConnectionError("temporary"), "Finally success"]
    
    tool_call = {"name": "test_tool", "args": {"input": "test"}, "id": "call_2"}
    
    result = await _execute_single_tool_safely(tool_call, [mock_tool])
    
    assert result.content == "Finally success"
    assert mock_tool.invoke.call_count == 3
    assert mock_sleep.call_count == 2
    # Sleep times: 1 * 2^0 = 1, 1 * 2^1 = 2
    mock_sleep.assert_any_call(1)
    mock_sleep.assert_any_call(2)

@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_execute_single_tool_safely_rate_limit_retry(mock_sleep):
    mock_tool = Mock()
    mock_tool.name = "test_tool"
    # Fail repeatedly with Rate Limit until MAX_RETRIES exhausted
    mock_tool.invoke.side_effect = Exception("429 Too Many Requests")
    
    tool_call = {"name": "test_tool", "args": {"input": "test"}, "id": "call_3"}
    
    result = await _execute_single_tool_safely(tool_call, [mock_tool])
    
    assert result.status == "error"
    assert "Tool execution failed after retries" in result.content
    assert mock_tool.invoke.call_count == MAX_RETRIES
    assert mock_sleep.call_count == MAX_RETRIES
    # Sleep times for rate limit: 2 * 2^0 = 2, 2 * 2^1 = 4, 2 * 2^2 = 8
    mock_sleep.assert_any_call(2)
    mock_sleep.assert_any_call(4)
    # mock_sleep.assert_any_call(8) # Wait, range is MAX_RETRIES (3), so attempts are 0, 1, 2. Sleep is called for 0, 1, 2.

@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_execute_single_tool_safely_permanent_error(mock_sleep):
    mock_tool = Mock()
    mock_tool.name = "test_tool"
    # Fail with a non-transient, non-rate-limit error (e.g., ValueError)
    mock_tool.invoke.side_effect = ValueError("Invalid tool argument")
    
    tool_call = {"name": "test_tool", "args": {"input": "test"}, "id": "call_4"}
    
    result = await _execute_single_tool_safely(tool_call, [mock_tool])
    
    assert result.status == "error"
    assert "Tool execution failed after retries" in result.content
    assert "Invalid tool argument" in result.content
    # Should break immediately, no retries
    assert mock_tool.invoke.call_count == 1
    mock_sleep.assert_not_called()

@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_execute_single_tool_safely_truncation(mock_sleep):
    mock_tool = Mock()
    mock_tool.name = "test_tool"
    # Return a massive string
    massive_string = "A" * (MAX_OUTPUT_TOKENS * 4 + 1000)
    mock_tool.invoke.return_value = massive_string
    
    tool_call = {"name": "test_tool", "args": {}, "id": "call_5"}
    
    result = await _execute_single_tool_safely(tool_call, [mock_tool])
    
    assert len(result.content) <= MAX_OUTPUT_TOKENS * 4 + len(TRUNCATE_WARNINGS)
    assert result.content.endswith(TRUNCATE_WARNINGS)

@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_execute_llm_query_safely_success(mock_sleep):
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AIMessage(content="LLM Success")
    
    result = await _execute_llm_query_safely(mock_llm, [])
    
    assert "messages" in result
    assert result["messages"].content == "LLM Success"
    mock_llm.ainvoke.assert_called_once()
    mock_sleep.assert_not_called()

@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_execute_llm_query_safely_timeout_retry(mock_sleep):
    mock_llm = AsyncMock()
    # LLM throws a timeout error twice, then succeeds
    mock_llm.ainvoke.side_effect = [TimeoutError(), TimeoutError(), AIMessage(content="Finally LLM Success")]
    
    result = await _execute_llm_query_safely(mock_llm, [])
    
    assert "messages" in result
    assert result["messages"].content == "Finally LLM Success"
    assert mock_llm.ainvoke.call_count == 3
    assert mock_sleep.call_count == 2

@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_execute_llm_query_safely_exhausts_retries(mock_sleep):
    mock_llm = AsyncMock()
    # LLM throws a timeout error repeatedly
    mock_llm.ainvoke.side_effect = TimeoutError()
    
    result = await _execute_llm_query_safely(mock_llm, [])
    
    assert "messages" in result
    assert "LLM request timeout exceeded" in result["messages"].content
    assert mock_llm.ainvoke.call_count == MAX_RETRIES
    assert mock_sleep.call_count == MAX_RETRIES

from langchain_core.messages import AIMessage
from tools.safe_execution import SafeToolNode

@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_safe_tool_node_non_write_tool(mock_execute, mock_interrupt):
    # Setup
    node = SafeToolNode(tools=[]) # Tool instances don't matter since we patch execution
    state = {
        "messages": [
            AIMessage(
                content="", 
                tool_calls=[{"name": "retrieve_training_sessions", "args": {}, "id": "call_1"}]
            )
        ]
    }
    mock_execute.return_value = ToolMessage(content="Retrieved data", tool_call_id="call_1")

    # Execution
    result = await node(state)

    # Verification
    mock_interrupt.assert_not_called()
    mock_execute.assert_called_once()
    assert len(result["messages"]) == 1
    assert result["messages"][0].content == "Retrieved data"

@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_safe_tool_node_write_tool_approved(mock_execute, mock_interrupt):
    # Setup
    node = SafeToolNode(tools=[])
    state = {
        "messages": [
            AIMessage(
                content="", 
                tool_calls=[{"name": "log_training_session", "args": {"note": "test"}, "id": "call_2"}]
            )
        ]
    }
    # Simulate user approving the interrupt
    mock_interrupt.return_value = {"approved": True}
    mock_execute.return_value = ToolMessage(content="Saved successfully", tool_call_id="call_2")

    # Execution
    result = await node(state)

    # Verification
    mock_interrupt.assert_called_once_with({
        "action": "approval_required",
        "tool_calls": [{"name": "log_training_session", "args": {"note": "test"}, "id": "call_2", "type": "tool_call"}]
    })
    mock_execute.assert_called_once()
    assert len(result["messages"]) == 1
    assert result["messages"][0].content == "Saved successfully"

@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_safe_tool_node_write_tool_rejected(mock_execute, mock_interrupt):
    # Setup
    node = SafeToolNode(tools=[])
    state = {
        "messages": [
            AIMessage(
                content="", 
                tool_calls=[{"name": "log_meal", "args": {"food": "apple"}, "id": "call_3"}]
            )
        ]
    }
    # Simulate user rejecting the interrupt
    mock_interrupt.return_value = {"approved": False}

    # Execution
    result = await node(state)

    # Verification
    mock_interrupt.assert_called_once()
    mock_execute.assert_not_called() # The tool must NOT be executed
    
    assert len(result["messages"]) == 1
    rejected_message = result["messages"][0]
    assert isinstance(rejected_message, ToolMessage)
    assert rejected_message.status == "error"
    assert "User rejected the operation" in rejected_message.content
    assert rejected_message.tool_call_id == "call_3"
