import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request
from api import chat_endpoint, ChatRequest, ChatResponse

@pytest.fixture
def mock_request():
    request = MagicMock(spec=Request)
    request.app.state.agent = AsyncMock()
    return request

@pytest.mark.asyncio
@patch("api.generate_conversational_approval", new_callable=AsyncMock)
async def test_chat_endpoint_with_interrupt(mock_gen_approval, mock_request):
    mock_gen_approval.return_value = "Conversational approval request."
    
    async def mock_astream(*args, **kwargs):
        msg = MagicMock()
        msg.content = "Hello world"
        yield {"chatter": {"messages": [msg]}}
        
        interrupt_val = MagicMock()
        interrupt_val.value = {"tool_calls": [{"name": "test_tool"}]}
        yield {"__interrupt__": [interrupt_val]}
        
    mock_request.app.state.agent.aget_state.return_value = None
    mock_request.app.state.agent.astream = mock_astream
    req = ChatRequest(user_id="user1", message="Do something")
    response = await chat_endpoint(req, mock_request)
    
    assert isinstance(response, ChatResponse)
    assert response.response == "Conversational approval request."
    mock_gen_approval.assert_called_once()

@pytest.mark.asyncio
@patch("api.classify_approval_intent", new_callable=AsyncMock)
@patch("api.generate_conversational_approval", new_callable=AsyncMock)
async def test_chat_endpoint_resume_interrupt(mock_gen_approval, mock_classifier, mock_request):
    mock_classifier.return_value = (True, "")
    mock_gen_approval.return_value = "Nested conversational approval."
    
    async def mock_astream(*args, **kwargs):
        msg = MagicMock()
        msg.content = "Creating new practice..."
        yield {"training": {"messages": [msg]}}
        
        interrupt_val = MagicMock()
        interrupt_val.value = {"tool_calls": [{"name": "log_training_session"}]}
        yield {"__interrupt__": [interrupt_val]}
        
    mock_state = MagicMock()
    mock_task = MagicMock()
    mock_intr = MagicMock()
    mock_intr.id = "intr_123"
    mock_task.interrupts = [mock_intr]
    mock_state.tasks = [mock_task]
    
    mock_request.app.state.agent.aget_state.return_value = mock_state
    mock_request.app.state.agent.astream = mock_astream

    req = ChatRequest(user_id="user1", message="Yes go ahead")
    response = await chat_endpoint(req, mock_request)
    
    assert isinstance(response, ChatResponse)
    assert response.response == "Nested conversational approval."
    mock_classifier.assert_called_once_with("Yes go ahead", mock_request.app.state.llm_config)

