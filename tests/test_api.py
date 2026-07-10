import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import Request
from api import chat_endpoint, resume_checkpoint, ChatRequest, ResumeRequest, ChatResponse

@pytest.fixture
def mock_request():
    request = MagicMock(spec=Request)
    request.app.state.agent = AsyncMock()
    return request

@pytest.mark.asyncio
async def test_chat_endpoint_with_interrupt(mock_request):
    async def mock_astream(*args, **kwargs):
        msg = MagicMock()
        msg.content = "I will save the data."
        yield {"chatter": {"messages": [msg]}}
        
        interrupt_val = MagicMock()
        interrupt_val.value = {"tool_calls": [{"name": "log_training_session"}]}
        yield {"__interrupt__": [interrupt_val]}
        
    mock_request.app.state.agent.astream = mock_astream

    req = ChatRequest(user_id="user1", message="Log my run")
    response = await chat_endpoint(req, mock_request)
    
    assert isinstance(response, ChatResponse)
    # The API should append [SYSTEM_APPROVAL]
    assert "[SYSTEM_APPROVAL]" in response.response
    assert "I will save the data." in response.response
    assert response.pending_tools == [{"name": "log_training_session"}]

@pytest.mark.asyncio
async def test_resume_checkpoint_with_nested_interrupt(mock_request):
    async def mock_astream(*args, **kwargs):
        msg = MagicMock()
        msg.content = "Creating new practice..."
        yield {"training": {"messages": [msg]}}
        
        interrupt_val = MagicMock()
        interrupt_val.value = {"tool_calls": [{"name": "log_training_session"}]}
        yield {"__interrupt__": [interrupt_val]}
        
    mock_request.app.state.agent.astream = mock_astream

    req = ResumeRequest(user_id="user1", approved=True)
    response = await resume_checkpoint(req, mock_request)
    
    assert isinstance(response, ChatResponse)
    assert "[SYSTEM_APPROVAL]" in response.response
    assert "Creating new practice..." in response.response
    assert response.pending_tools == [{"name": "log_training_session"}]
