from tools.safe_execution import SafeToolNode
import asyncio
import os
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, AsyncMock, patch

from langchain_core.messages import ToolMessage, AIMessage, HumanMessage
from langgraph.graph import END

from tools.safe_execution import (
    _is_transient_tool_error,
    _is_rate_limit_tool_error,
    _execute_single_tool_safely,
    _execute_llm_query_safely,
    ApprovalDecision,
    ApprovalIntentModel,
    ApprovalResolver,
    PendingReplyClassifier,
    PendingReplyKind,
    HITL_TIMEOUT_SECONDS,
    hitl_interrupt_payload_expired,
    route_after_hitl_cancel,
    MAX_RETRIES,
    MAX_OUTPUT_TOKENS,
    TRUNCATE_WARNINGS,
)
from agents.observability import InMemorySink, observation_sink, start_trace
from agents.llm_factory import LLMConfig
from agents.roles.training import INSTRUCTION_FOR_RECORDING_TRAINING_SESSIONS
from langchain_core.prompts.prompt import PromptTemplate


class FakeApprovalResolver:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    async def resolve(self, user_message, pending_tool_calls):
        self.calls.append((user_message, pending_tool_calls))
        return self.decision


def test_recording_prompt_explains_how_to_handle_superseded_training_writes():
    prompt = PromptTemplate.from_template(
        INSTRUCTION_FOR_RECORDING_TRAINING_SESSIONS
    ).format(current_time="2026-07-31")
    normalized_prompt = " ".join(prompt.split())

    assert "superseded" in normalized_prompt
    assert "merge" in normalized_prompt
    assert "new approval" in normalized_prompt


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
    mock_tool.invoke.side_effect = [
        ConnectionError("temporary"),
        ConnectionError("temporary"),
        "Finally success",
    ]

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
    assert "Invalid tool argument" not in result.content
    # Should break immediately, no retries
    assert mock_tool.invoke.call_count == 1
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_failed_tool_message_marks_tool_span_error(mock_sleep):
    mock_tool = Mock()
    mock_tool.name = "test_tool"
    mock_tool.invoke.side_effect = ValueError("Invalid tool argument")
    tool_call = {"name": "test_tool", "args": {"input": "test"}, "id": "call_error"}
    sink = InMemorySink()

    with observation_sink(sink):
        with start_trace(
            "chat.request",
            request_id="request-1",
            session_id="session-1",
            user_key="user-key",
        ):
            result = await _execute_single_tool_safely(tool_call, [mock_tool])

    tool_span = next(
        observation
        for observation in sink.observations
        if observation.signal == "span.end" and observation.name == "tool.test_tool"
    )
    assert result.status == "error"
    assert tool_span.status == "error"
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
    mock_llm.ainvoke.side_effect = [
        TimeoutError(),
        TimeoutError(),
        AIMessage(content="Finally LLM Success"),
    ]

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
    assert "LLM request failed after retries" in result["messages"].content
    assert mock_llm.ainvoke.call_count == MAX_RETRIES
    assert mock_sleep.call_count == MAX_RETRIES


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_exhausted_llm_retries_mark_llm_span_error(mock_sleep):
    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = TimeoutError()
    sink = InMemorySink()

    with observation_sink(sink):
        with start_trace(
            "chat.request",
            request_id="request-1",
            session_id="session-1",
            user_key="user-key",
        ):
            result = await _execute_llm_query_safely(mock_llm, [])

    llm_span = next(
        observation
        for observation in sink.observations
        if observation.signal == "span.end" and observation.name == "llm.request"
    )
    assert "[Error]" in result["messages"].content
    assert llm_span.status == "error"
    assert mock_sleep.call_count == MAX_RETRIES


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_safe_tool_node_non_write_tool(mock_execute, mock_interrupt):
    # Setup
    node = SafeToolNode(
        tools=[]
    )  # Tool instances don't matter since we patch execution
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "retrieve_training_sessions", "args": {}, "id": "call_1"}
                ],
            )
        ]
    }
    mock_execute.return_value = ToolMessage(
        content="Retrieved data", tool_call_id="call_1"
    )

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
                tool_calls=[
                    {
                        "name": "log_training_session",
                        "args": {"note": "test"},
                        "id": "call_2",
                    }
                ],
            )
        ]
    }
    # Simulate user approving the interrupt
    mock_interrupt.return_value = {"approved": True}
    mock_execute.return_value = ToolMessage(
        content="Saved successfully", tool_call_id="call_2"
    )

    # Execution
    result = await node(state)

    # Verification
    mock_interrupt.assert_called_once()
    payload = mock_interrupt.call_args.args[0]
    assert payload["action"] == "approval_required"
    assert payload["tool_calls"] == [
        {
            "name": "log_training_session",
            "args": {"note": "test"},
            "id": "call_2",
            "type": "tool_call",
        }
    ]
    created_at = datetime.fromisoformat(payload["created_at"])
    assert created_at.tzinfo is not None
    mock_execute.assert_called_once()
    assert len(result["messages"]) == 1
    assert result["messages"][0].content == "Saved successfully"


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_pure_approval_injects_operation_id_without_mutating_pending_call(
    mock_execute, mock_interrupt
):
    resolver = FakeApprovalResolver(ApprovalDecision(intent="approve", feedback="保存"))
    node = SafeToolNode(tools=[], approval_resolver=resolver)
    pending = {
        "name": "log_training_session",
        "args": {"note": "test"},
        "id": "training-1",
    }
    mock_interrupt.return_value = {"user_message": "保存"}
    mock_execute.return_value = ToolMessage(content="Saved", tool_call_id="training-1")

    await node({"messages": [AIMessage(content="", tool_calls=[pending])]})

    executed = mock_execute.await_args.args[0]
    assert executed["args"]["operation_id"] == "hitl:training-1"
    assert "operation_id" not in pending["args"]


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
                tool_calls=[
                    {"name": "log_meal", "args": {"food": "apple"}, "id": "call_3"}
                ],
            )
        ]
    }
    # Simulate user rejecting the interrupt
    mock_interrupt.return_value = {"approved": False}

    # Execution
    result = await node(state)

    # Verification
    mock_interrupt.assert_called_once()
    mock_execute.assert_not_called()  # The tool must NOT be executed

    assert len(result["messages"]) == 1
    rejected_message = result["messages"][0]
    assert isinstance(rejected_message, ToolMessage)
    assert rejected_message.status == "error"
    assert "User rejected the operation" in rejected_message.content
    assert rejected_message.tool_call_id == "call_3"


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_revision_supersedes_write_and_preserves_reply(
    mock_execute, mock_interrupt
):
    resolver = FakeApprovalResolver(
        ApprovalDecision(intent="revise", feedback="保存，同时 RPE 7")
    )
    node = SafeToolNode(tools=[], approval_resolver=resolver)
    pending = {
        "name": "log_training_session",
        "args": {"sessions": [{"rpe": None}]},
        "id": "training-1",
    }
    mock_interrupt.return_value = {"user_message": "保存，同时 RPE 7"}

    result = await node({"messages": [AIMessage(content="", tool_calls=[pending])]})

    mock_execute.assert_not_called()
    assert resolver.calls == [("保存，同时 RPE 7", [pending])]
    assert isinstance(result["messages"][0], ToolMessage)
    assert result["messages"][0].status == "error"
    assert "superseded" in result["messages"][0].content
    assert isinstance(result["messages"][1], HumanMessage)
    assert result["messages"][1].content == "保存，同时 RPE 7"


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_training_revision_requires_fresh_approval_before_single_write(
    mock_execute, mock_interrupt
):
    resolver = FakeApprovalResolver(
        ApprovalDecision(intent="revise", feedback="保存，同时 RPE 7")
    )
    node = SafeToolNode(tools=[], approval_resolver=resolver)
    original_call = {
        "name": "log_training_session",
        "args": {"sessions": [{"rpe": None}]},
        "id": "training-original",
    }
    replacement_call = {
        "name": "log_training_session",
        "args": {"sessions": [{"rpe": 7}]},
        "id": "training-revised",
    }
    mock_interrupt.side_effect = [
        {"user_message": "保存，同时 RPE 7"},
        {"user_message": "保存"},
    ]

    assert original_call["args"]["sessions"][0]["rpe"] is None
    revision_result = await node(
        {"messages": [AIMessage(content="", tool_calls=[original_call])]}
    )

    mock_execute.assert_not_called()
    assert resolver.calls == [("保存，同时 RPE 7", [original_call])]
    assert isinstance(revision_result["messages"][0], ToolMessage)
    assert "superseded" in revision_result["messages"][0].content
    assert isinstance(revision_result["messages"][1], HumanMessage)
    assert revision_result["messages"][1].content == "保存，同时 RPE 7"

    resolver.decision = ApprovalDecision(intent="approve", feedback="保存")
    mock_execute.return_value = ToolMessage(
        content="Saved", tool_call_id="training-revised"
    )
    await node({"messages": [AIMessage(content="", tool_calls=[replacement_call])]})

    assert mock_interrupt.call_count == 2
    second_interrupt = mock_interrupt.call_args_list[1].args[0]
    assert second_interrupt["tool_calls"][0]["args"]["sessions"][0]["rpe"] == 7
    mock_execute.assert_awaited_once()
    assert (
        mock_execute.await_args.args[0]["args"]["operation_id"]
        == "hitl:training-revised"
    )


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
async def test_raw_rejection_preserves_complete_feedback(mock_interrupt):
    resolver = FakeApprovalResolver(
        ApprovalDecision(intent="reject", feedback="不要保存，今天其实没训练")
    )
    node = SafeToolNode(tools=[], approval_resolver=resolver)
    mock_interrupt.return_value = {"user_message": "不要保存，今天其实没训练"}

    result = await node(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "log_training_session",
                            "args": {},
                            "id": "training-1",
                        }
                    ],
                )
            ]
        }
    )

    assert "不要保存，今天其实没训练" in result["messages"][0].content


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
async def test_resumed_hitl_emits_resolved_revision_without_raw_reply(mock_interrupt):
    user_reply = "保存，同时 RPE 7"
    resolver = FakeApprovalResolver(
        ApprovalDecision(intent="revise", feedback=user_reply)
    )
    node = SafeToolNode(tools=[], approval_resolver=resolver)
    mock_interrupt.return_value = {"user_message": user_reply}
    sink = InMemorySink()

    with observation_sink(sink):
        with start_trace(
            "chat.request",
            request_id="request-1",
            session_id="session-1",
            user_key="user-key",
        ):
            await node(
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "log_training_session",
                                    "args": {},
                                    "id": "training-1",
                                }
                            ],
                        )
                    ]
                }
            )

    resumed = next(
        observation
        for observation in sink.observations
        if observation.name == "hitl.resumed"
    )
    assert resumed.attributes == {
        "decision": "revised",
        "tool.count": 1,
        "tool.call_ids": ["training-1"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "model_intent"),
    [
        ("是的", "approve"),
        ("确认保存", "approve"),
        ("当然，就这样保存吧", "approve"),
        ("是的，但重量改成 14kg", "revise"),
        ("先别保存", "reject"),
    ],
)
async def test_approval_resolver_uses_structured_lm_semantics_for_every_reply(
    monkeypatch, reply, model_intent
):
    captured = {}

    class FakeStructuredRunnable:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return ApprovalIntentModel(intent=model_intent)

    class FakeChatModel:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return FakeStructuredRunnable()

    monkeypatch.setattr(
        "tools.safe_execution.create_chat_model", lambda config: FakeChatModel()
    )
    resolver = ApprovalResolver(Mock())

    decision = await resolver.resolve(reply, [{"name": "log_meal", "args": {}}])

    assert decision == ApprovalDecision(intent=model_intent, feedback=reply)
    assert captured["schema"] is ApprovalIntentModel
    assert reply in captured["messages"][1].content


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.parametrize(
    ("reply", "expected_intent"),
    [
        ("是的", "approve"),
        ("确认保存", "approve"),
        ("当然，就这样保存吧", "approve"),
        ("是的，但重量改成 14kg", "revise"),
        ("先别保存", "reject"),
    ],
)
async def test_live_google_approval_resolver_understands_reply_semantics(
    reply, expected_intent
):
    if not os.environ.get("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY is required for live approval semantics")
    proxy = os.environ.get("LLM_PROXY")
    kwargs = {"client_args": {"proxy": proxy}} if proxy else {}
    resolver = ApprovalResolver(
        LLMConfig(
            provider="google",
            model_name="gemini-3.5-flash",
            temperature=0,
            max_tokens=1024,
            kwargs=kwargs,
        )
    )

    decision = await resolver.resolve(
        reply,
        [
            {
                "name": "log_training_session",
                "args": {"weight": 10.0, "reps": [5]},
                "id": "pending-training",
            }
        ],
    )

    assert decision.intent == expected_intent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "确认，RPE 改成 7",
        "保存，同时 RPE 7",
        "是的，RPE 改成 7",
        "对，但重量是 14kg",
        "好的，同时备注肩膀不舒服",
        "确认？",
        "保存...",
    ],
)
@patch("tools.safe_execution._execute_llm_query_safely")
@patch("tools.safe_execution.create_chat_model")
async def test_approval_resolver_sends_reply_to_classifier(
    mock_create_chat_model, mock_execute, reply
):
    mock_execute.return_value = {"messages": ApprovalIntentModel(intent="revise")}
    resolver = ApprovalResolver(Mock())

    decision = await resolver.resolve(reply, [{"name": "log_meal", "args": {}}])

    assert decision == ApprovalDecision(intent="revise", feedback=reply)
    mock_execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["确认保存", "是的", "当然，就这样保存吧"])
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
@patch("tools.safe_execution._execute_llm_query_safely")
@patch("tools.safe_execution.create_chat_model")
async def test_semantic_lm_approval_executes_original_pending_write_once(
    mock_create_chat_model, mock_llm_query, mock_tool_execute, mock_interrupt, reply
):
    mock_llm_query.return_value = {"messages": ApprovalIntentModel(intent="approve")}
    mock_interrupt.return_value = {"user_message": reply}
    mock_tool_execute.return_value = ToolMessage(
        content="Saved", tool_call_id="training-1"
    )
    node = SafeToolNode(tools=[], approval_resolver=ApprovalResolver(Mock()))
    pending = {
        "name": "log_training_session",
        "args": {"note": "test"},
        "id": "training-1",
    }

    result = await node({"messages": [AIMessage(content="", tool_calls=[pending])]})

    mock_create_chat_model.return_value.with_structured_output.assert_called_once_with(
        ApprovalIntentModel
    )
    mock_llm_query.assert_awaited_once()
    mock_tool_execute.assert_awaited_once()
    assert result["messages"][0].content == "Saved"


@pytest.mark.asyncio
@patch("tools.safe_execution._execute_llm_query_safely")
@patch("tools.safe_execution.create_chat_model")
async def test_approval_resolver_returns_revision_and_preserves_full_feedback(
    mock_create_chat_model, mock_execute
):
    mock_execute.return_value = {"messages": ApprovalIntentModel(intent="revise")}
    resolver = ApprovalResolver(Mock())

    decision = await resolver.resolve(
        "保存，同时 RPE 7",
        [{"name": "log_training_session", "args": {"rpe": None}, "id": "1"}],
    )

    assert decision == ApprovalDecision(intent="revise", feedback="保存，同时 RPE 7")


@pytest.mark.asyncio
@patch("tools.safe_execution._execute_llm_query_safely")
@patch("tools.safe_execution.create_chat_model")
async def test_approval_resolver_safely_rejects_malformed_structured_output(
    mock_create_chat_model, mock_execute
):
    mock_execute.return_value = {"messages": {"unexpected": "value"}}
    resolver = ApprovalResolver(Mock())

    decision = await resolver.resolve("不保存", [])

    assert decision == ApprovalDecision(intent="reject", feedback="不保存")


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_safe_tool_node_rejects_every_parallel_write_tool(
    mock_execute, mock_interrupt
):
    node = SafeToolNode(tools=[])
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "log_meal", "args": {"food": "apple"}, "id": "meal-1"},
                    {
                        "name": "log_training_session",
                        "args": {"exercise": "run"},
                        "id": "training-1",
                    },
                ],
            )
        ]
    }
    mock_interrupt.return_value = {"approved": False}

    result = await node(state)

    mock_execute.assert_not_called()
    assert [message.tool_call_id for message in result["messages"]] == [
        "meal-1",
        "training-1",
    ]
    assert all(message.status == "error" for message in result["messages"])


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_safe_tool_node_rejects_write_and_executes_parallel_read_tool(
    mock_execute, mock_interrupt
):
    node = SafeToolNode(tools=[])
    write_call = {
        "name": "log_meal",
        "args": {"food": "apple"},
        "id": "meal-1",
    }
    read_call = {
        "name": "retrieve_training_sessions",
        "args": {},
        "id": "training-read-1",
    }
    state = {
        "messages": [
            AIMessage(content="", tool_calls=[write_call, read_call]),
        ]
    }
    mock_interrupt.return_value = {"approved": False}
    mock_execute.return_value = ToolMessage(
        content="history",
        tool_call_id="training-read-1",
    )

    result = await node(state)

    mock_execute.assert_awaited_once()
    assert mock_execute.await_args.args[0]["id"] == "training-read-1"
    assert [message.tool_call_id for message in result["messages"]] == [
        "meal-1",
        "training-read-1",
    ]
    assert result["messages"][0].status == "error"
    assert result["messages"][1].content == "history"


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_resumed_hitl_execution_finishes_trace_ok_and_emits_executed(
    mock_execute, mock_interrupt
):
    node = SafeToolNode(tools=[])
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "log_meal", "args": {"food": "apple"}, "id": "call-1"}
                ],
            )
        ]
    }
    mock_interrupt.return_value = {"approved": True}
    mock_execute.return_value = ToolMessage(content="saved", tool_call_id="call-1")
    sink = InMemorySink()

    with observation_sink(sink):
        with start_trace(
            "chat.request",
            request_id="request-1",
            session_id="session-1",
            user_key="user-key",
        ):
            await node(state)

    root_end = next(
        observation
        for observation in sink.observations
        if observation.signal == "span.end" and observation.name == "chat.request"
    )
    resumed = next(
        observation
        for observation in sink.observations
        if observation.name == "hitl.resumed"
    )
    assert root_end.status == "ok"
    assert resumed.attributes == {
        "decision": "approved",
        "tool.count": 1,
        "tool.call_ids": ["call-1"],
    }
    assert any(observation.name == "hitl.executed" for observation in sink.observations)


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_rejected_hitl_emits_cancelled_without_execution(
    mock_execute, mock_interrupt
):
    node = SafeToolNode(tools=[])
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "log_meal", "args": {"food": "apple"}, "id": "call-1"}
                ],
            )
        ]
    }
    mock_interrupt.return_value = {"approved": False}
    sink = InMemorySink()

    with observation_sink(sink):
        with start_trace(
            "chat.request",
            request_id="request-1",
            session_id="session-1",
            user_key="user-key",
        ):
            await node(state)

    mock_execute.assert_not_called()
    assert any(
        observation.name == "hitl.cancelled" for observation in sink.observations
    )


def test_hitl_interrupt_payload_expired_detects_stale_requests():
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    fresh = {"created_at": (now - timedelta(seconds=299)).isoformat()}
    stale = {"created_at": (now - timedelta(seconds=301)).isoformat()}

    assert hitl_interrupt_payload_expired(stale, now=now)
    assert not hitl_interrupt_payload_expired(fresh, now=now)


def test_hitl_interrupt_payload_expired_treats_legacy_payloads_as_fresh():
    """Breaks if interrupts persisted without created_at hijack fresh messages."""

    now = datetime.now(timezone.utc)
    assert not hitl_interrupt_payload_expired({"tool_calls": []}, now=now)
    assert not hitl_interrupt_payload_expired({"created_at": None}, now=now)
    assert not hitl_interrupt_payload_expired({"created_at": 12345}, now=now)
    assert not hitl_interrupt_payload_expired({}, now=now)
    assert not hitl_interrupt_payload_expired(None, now=now)
    assert not hitl_interrupt_payload_expired("interrupt", now=now)
    assert not hitl_interrupt_payload_expired(
        {"created_at": "not-a-timestamp"}, now=now
    )


def test_hitl_interrupt_payload_expired_assumes_naive_timestamps_are_utc():
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    naive_stale = {"created_at": "2020-01-01T00:00:00"}

    assert hitl_interrupt_payload_expired(naive_stale, now=now)


def test_hitl_interrupt_payload_expired_honors_custom_timeout():
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "created_at": (now - timedelta(seconds=HITL_TIMEOUT_SECONDS / 2)).isoformat()
    }

    assert hitl_interrupt_payload_expired(payload, now=now, timeout_seconds=60.0)
    assert not hitl_interrupt_payload_expired(payload, now=now)


def test_route_after_hitl_cancel_ends_graph_only_after_terminal_cancellation():
    assert route_after_hitl_cancel({"hitl_write_cancelled": True}, "log_meal") == END
    assert route_after_hitl_cancel({}, "log_meal") == "log_meal"
    assert (
        route_after_hitl_cancel({"hitl_write_cancelled": False}, "log_meal")
        == "log_meal"
    )


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_cancel_payload_supersedes_pending_write_without_execution(
    mock_execute, mock_interrupt
):
    """Breaks if a stale pending approval still executes after terminal cancel."""

    node = SafeToolNode(tools=[])
    pending = {"name": "log_meal", "args": {"items": "香蕉"}, "id": "meal-1"}
    mock_interrupt.return_value = {"cancelled": True, "feedback": "approval_expired"}

    result = await node({"messages": [AIMessage(content="", tool_calls=[pending])]})

    mock_execute.assert_not_called()
    assert result["hitl_write_cancelled"] is True
    assert len(result["messages"]) == 1
    cancelled_message = result["messages"][0]
    assert isinstance(cancelled_message, ToolMessage)
    assert cancelled_message.status == "error"
    assert "Pending write cancelled" in cancelled_message.content
    assert "approval_expired" in cancelled_message.content
    assert cancelled_message.tool_call_id == "meal-1"


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_cancelled_hitl_executes_parallel_read_tool_but_not_write(
    mock_execute, mock_interrupt
):
    node = SafeToolNode(tools=[])
    write_call = {"name": "log_meal", "args": {"items": "苹果"}, "id": "meal-1"}
    read_call = {"name": "retrieve_training_sessions", "args": {}, "id": "read-1"}
    mock_interrupt.return_value = {
        "cancelled": True,
        "feedback": "superseded_by_new_request",
    }
    mock_execute.return_value = ToolMessage(content="history", tool_call_id="read-1")

    result = await node(
        {"messages": [AIMessage(content="", tool_calls=[write_call, read_call])]}
    )

    mock_execute.assert_awaited_once()
    assert mock_execute.await_args.args[0]["id"] == "read-1"
    assert [message.tool_call_id for message in result["messages"]] == [
        "meal-1",
        "read-1",
    ]
    assert result["messages"][0].status == "error"
    assert result["messages"][1].content == "history"
    assert result["hitl_write_cancelled"] is True


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
async def test_cancelled_hitl_emits_stale_cancelled_without_execution(mock_interrupt):
    node = SafeToolNode(tools=[])
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"name": "log_meal", "args": {}, "id": "meal-1"}],
            )
        ]
    }
    mock_interrupt.return_value = {"cancelled": True, "feedback": "approval_expired"}
    sink = InMemorySink()

    with observation_sink(sink):
        with start_trace(
            "chat.request",
            request_id="request-1",
            session_id="session-1",
            user_key="user-key",
        ):
            await node(state)

    stale_cancelled = next(
        observation
        for observation in sink.observations
        if observation.name == "hitl.stale_cancelled"
    )
    assert stale_cancelled.attributes == {
        "hitl.cancel.reason": "approval_expired",
        "tool.count": 1,
        "tool.call_ids": ["meal-1"],
    }
    assert not any(
        observation.name == "hitl.resumed" for observation in sink.observations
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "expected_kind"),
    [
        ("确认保存", "approval_reply"),
        ("保存，但重量改成 14kg", "approval_reply"),
        ("先别保存", "approval_reply"),
        ("我昨天练了 mace，5kg 100 次", "new_request"),
        ("今天天气怎么样", "new_request"),
    ],
)
async def test_pending_reply_classifier_uses_structured_lm_semantics(
    monkeypatch, reply, expected_kind
):
    captured = {}

    class FakeStructuredRunnable:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return PendingReplyKind(kind=expected_kind)

    class FakeChatModel:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return FakeStructuredRunnable()

    monkeypatch.setattr(
        "tools.safe_execution.create_chat_model", lambda config: FakeChatModel()
    )
    classifier = PendingReplyClassifier(Mock())

    kind = await classifier.classify(
        reply, [{"name": "log_meal", "args": {"items": "香蕉"}, "id": "meal-1"}]
    )

    assert kind == expected_kind
    assert captured["schema"] is PendingReplyKind
    assert reply in captured["messages"][1].content
    assert "log_meal" in captured["messages"][1].content


@pytest.mark.asyncio
@patch("tools.safe_execution._execute_llm_query_safely")
@patch("tools.safe_execution.create_chat_model")
async def test_pending_reply_classifier_defaults_to_approval_reply_on_malformed_output(
    mock_create_chat_model, mock_execute
):
    """Breaks if classifier failures strand the user's reply outside the graph."""

    mock_execute.return_value = {"messages": {"unexpected": "value"}}
    classifier = PendingReplyClassifier(Mock())

    kind = await classifier.classify("我昨天练了 mace，5kg 100 次", [])

    assert kind == "approval_reply"
