import asyncio
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.graph import END
from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from agents.llm_factory import LLMConfig, create_chat_model
from agents.observability import (
    emit_event,
    error_attributes,
    mark_current_span_status,
    observe_span,
)

MAX_RETRIES = 3
MAX_TOOL_TOKENS = 5000
LLM_TIMEOUT_SECONDS = 30.0
MIN_TOOL_EXECUTION_TOKENS = 50
MAX_OUTPUT_TOKENS = 500
TRUNCATE_WARNINGS = "\n[OUTPUT TRUNCATED - the tool returned more data than can be processed. Please ask a more specific question]"
HITL_TIMEOUT_SECONDS = 300.0  # 5 minutes for human-in-the-loop timeout
HITL_TOOL_CALLS = ["log_training_session", "log_meal"]


class ApprovalDecision(BaseModel):
    intent: Literal["approve", "revise", "reject"]
    feedback: str


class ApprovalResolverProtocol(Protocol):
    async def resolve(
        self, user_message: str, pending_tool_calls: list[dict]
    ) -> ApprovalDecision: ...


class ApprovalIntentModel(BaseModel):
    intent: Literal["approve", "revise", "reject"]


class ApprovalResolver:
    def __init__(self, llm_config: LLMConfig):
        chat_model = create_chat_model(llm_config)
        self.llm = chat_model.with_structured_output(ApprovalIntentModel)

    async def resolve(
        self, user_message: str, pending_tool_calls: list[dict]
    ) -> ApprovalDecision:
        instruction = (
            "Semantically classify a reply to a pending database-write approval. "
            "Judge the reply's meaning rather than matching exact words. Choose "
            "approve only when the reply solely accepts the exact pending data; "
            "this includes concise or conversational affirmations in any language, "
            "such as 是的, 确认保存, or 当然，就这样保存吧. Choose revise when "
            "the reply adds, corrects, removes, or replaces any business data, even "
            "if it also expresses approval. Choose reject when it declines or "
            "postpones the write, or when its meaning is unclear."
        )
        context = json.dumps(pending_tool_calls, ensure_ascii=False, default=str)
        resolver_messages = [
            SystemMessage(content=instruction),
            HumanMessage(
                content=f"Pending tool calls: {context}\nUser reply: {user_message}"
            ),
        ]
        response = await _execute_llm_query_safely(self.llm, resolver_messages)
        try:
            intent = ApprovalIntentModel.model_validate(response["messages"]).intent
        except (ValueError, TypeError, ValidationError):
            intent = "reject"
        return ApprovalDecision(intent=intent, feedback=user_message)


class PendingReplyKind(BaseModel):
    kind: Literal["approval_reply", "new_request"]


class PendingReplyClassifier:
    """Decide whether a message answers a pending approval or starts a new topic."""

    def __init__(self, llm_config: LLMConfig):
        chat_model = create_chat_model(llm_config)
        self.llm = chat_model.with_structured_output(PendingReplyKind)

    async def classify(self, user_message: str, pending_tool_calls: list[dict]) -> str:
        instruction = (
            "The assistant has asked the user to approve pending database writes. "
            "Classify the user's latest message. Choose approval_reply when the "
            "message responds to that approval request in any way: accepting it "
            "(e.g. 是的, 确认保存, sounds good), declining or postponing it (e.g. 取消, "
            "先别保存), or correcting and supplementing the pending data (e.g. 保存，"
            "但重量改成 14kg). Choose new_request when the message introduces an "
            "unrelated new topic or request (a workout, a meal, a question, small "
            "talk) and does not address the pending approval at all. When in doubt, "
            "choose approval_reply."
        )
        context = json.dumps(pending_tool_calls, ensure_ascii=False, default=str)
        classifier_messages = [
            SystemMessage(content=instruction),
            HumanMessage(
                content=f"Pending tool calls: {context}\nUser reply: {user_message}"
            ),
        ]
        response = await _execute_llm_query_safely(self.llm, classifier_messages)
        try:
            return PendingReplyKind.model_validate(response["messages"]).kind
        except (ValueError, TypeError, ValidationError):
            return "approval_reply"


def hitl_interrupt_payload_expired(
    payload: Any,
    *,
    now: datetime | None = None,
    timeout_seconds: float = HITL_TIMEOUT_SECONDS,
) -> bool:
    """Return True when an interrupt payload carries a creation time older than the HITL timeout.

    Payloads without a parseable ``created_at`` (e.g. interrupts persisted by older
    deployments) are treated as fresh so they still go through reply classification.
    """

    if not isinstance(payload, dict):
        return False
    created_at = payload.get("created_at")
    if not isinstance(created_at, str):
        return False
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - created) > timedelta(seconds=timeout_seconds)


def route_after_hitl_cancel(state: Mapping[str, Any], default_target: str) -> str:
    """Route a tool node to END after a pending write was terminally cancelled."""

    if state.get("hitl_write_cancelled"):
        return END
    return default_target


def _is_transient_tool_error(error: Exception) -> bool:
    """Return True if a tool execution is likely transient"""
    if isinstance(error, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    if isinstance(error, OSError):
        return True
    error_text = str(error).lower()
    return any(
        token in error_text
        for token in ("timeout", "temporary", "connection reset", "connection closed")
    )


def _is_rate_limit_tool_error(error: Exception) -> bool:
    """Return True if a tool execution indicates rate limit"""
    error_text = str(error).lower()
    return any(
        token in error_text for token in ("rate limit", "429", "too many requests")
    )


async def _execute_single_tool_safely(
    tool_call: dict, tool_list: Sequence[Any]
) -> ToolMessage:
    """Safely execute tool call, catch exception, and return mild message"""
    tool_name = tool_call["name"]
    tool_args = tool_call["args"]
    tool_id = tool_call["id"]

    with observe_span(
        f"tool.{tool_name}",
        {
            "tool.name": tool_name,
            "tool.call_id": tool_id,
            "tool.arg_keys": sorted(str(key) for key in tool_args),
            "tool.write": tool_name in HITL_TOOL_CALLS,
        },
    ):
        # tool must be an instance of tool_list
        tool_instance = next((t for t in tool_list if t.name == tool_name), None)
        if not tool_instance:
            emit_event("tool.not_found", {"tool.name": tool_name})
            mark_current_span_status("error")
            return ToolMessage(
                content="[Error] Tool tool_name not found.",
                name=tool_name,
                tool_call_id=tool_id,
                status="error",
            )

        # tool call with retry
        for attempt in range(MAX_RETRIES):
            try:
                emit_event(
                    "tool.attempt",
                    {"tool.name": tool_name, "tool.attempt": attempt + 1},
                )
                result = await asyncio.to_thread(tool_instance.invoke, tool_args)
                result_str = str(result)
                max_output_length = MAX_OUTPUT_TOKENS * 4
                truncated = len(result_str) > max_output_length
                if truncated:
                    result_str = result_str[:max_output_length] + TRUNCATE_WARNINGS
                emit_event(
                    "tool.completed",
                    {
                        "tool.name": tool_name,
                        "tool.attempt": attempt + 1,
                        "tool.output_chars": len(result_str),
                        "tool.output_truncated": truncated,
                    },
                )
                return ToolMessage(
                    content=result_str, name=tool_name, tool_call_id=tool_id
                )

            except Exception as error:
                retryable = _is_transient_tool_error(
                    error
                ) or _is_rate_limit_tool_error(error)
                emit_event(
                    "tool.attempt_failed",
                    {
                        "tool.name": tool_name,
                        "tool.attempt": attempt + 1,
                        "tool.retryable": retryable,
                        **error_attributes(error),
                    },
                )
                if _is_transient_tool_error(error):
                    await asyncio.sleep(1 * (2**attempt))
                    continue
                if _is_rate_limit_tool_error(error):
                    await asyncio.sleep(2 * (2**attempt))
                    continue
                break

        mark_current_span_status("error")
        error_msg = "[Error] Tool execution failed after retries."
        return ToolMessage(
            content=error_msg, name=tool_name, tool_call_id=tool_id, status="error"
        )


async def _execute_llm_query_safely(llm_with_tools, messages) -> dict:
    model_name = getattr(llm_with_tools, "model_name", None)
    if not isinstance(model_name, str):
        model_name = getattr(llm_with_tools, "model", None)
    model_attribute = model_name if isinstance(model_name, str) else None
    with observe_span(
        "llm.request",
        {
            "llm.class": type(llm_with_tools).__name__,
            "llm.model": model_attribute,
            "llm.message_count": len(messages),
            "llm.timeout_seconds": LLM_TIMEOUT_SECONDS,
        },
    ):
        for attempt in range(MAX_RETRIES):
            try:
                emit_event("llm.attempt", {"llm.attempt": attempt + 1})
                async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
                    response = await llm_with_tools.ainvoke(messages)
                    usage = getattr(response, "usage_metadata", None)
                    usage_attributes = (
                        {
                            f"llm.{key}": value
                            for key, value in usage.items()
                            if key
                            in {
                                "input_tokens",
                                "output_tokens",
                                "total_tokens",
                            }
                        }
                        if isinstance(usage, dict)
                        else {}
                    )
                    emit_event(
                        "llm.completed",
                        {"llm.attempt": attempt + 1, **usage_attributes},
                    )
                    return {"messages": response}
            except Exception as error:
                retryable = _is_transient_tool_error(
                    error
                ) or _is_rate_limit_tool_error(error)
                emit_event(
                    "llm.attempt_failed",
                    {
                        "llm.attempt": attempt + 1,
                        "llm.retryable": retryable,
                        **error_attributes(error),
                    },
                )
                if _is_transient_tool_error(error):
                    await asyncio.sleep(1 * (2**attempt))
                    continue
                if _is_rate_limit_tool_error(error):
                    await asyncio.sleep(2 * (2**attempt))
                    continue
                break

        mark_current_span_status("error")
        error_text = "[Error] LLM request failed after retries."
        response = AIMessage(content=error_text)
        return {"messages": response}


async def _build_write_rejection_messages(
    tool_calls: list[dict],
    write_tool_call_ids: set[str],
    write_content: str,
    tool_list: Sequence[Any],
) -> list[ToolMessage]:
    """Reject every pending write tool and still execute the parallel read tools."""

    read_tool_calls = [
        tool_call
        for tool_call in tool_calls
        if str(tool_call["id"]) not in write_tool_call_ids
    ]
    read_outputs = await asyncio.gather(
        *[
            _execute_single_tool_safely(tool_call, tool_list)
            for tool_call in read_tool_calls
        ]
    )
    read_outputs_by_id = {str(output.tool_call_id): output for output in read_outputs}
    return [
        (
            ToolMessage(
                name=tool_call["name"],
                tool_call_id=tool_call["id"],
                content=write_content,
                status="error",
            )
            if str(tool_call["id"]) in write_tool_call_ids
            else read_outputs_by_id[str(tool_call["id"])]
        )
        for tool_call in tool_calls
    ]


class SafeToolNode:
    """Callable, wraps safe tool call and can be used like a LangGraph ToolNode"""

    # using Sequence to accept list or tuple of tools
    def __init__(
        self,
        tools: Sequence[Any],
        approval_resolver: ApprovalResolverProtocol | None = None,
    ):
        self.tools = tools
        self.approval_resolver = approval_resolver

    async def __call__(self, state: dict, config: RunnableConfig | None = None) -> dict:
        messages = state.get("messages", [])
        if not messages:
            return {"messages": []}

        last_message = messages[-1]
        # decide if LLM calls the tool
        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return {"messages": []}

        tool_calls = getattr(last_message, "tool_calls", [])

        # tools that can write
        write_tools = []
        for tool_call in tool_calls:
            if any(keyword in tool_call["name"].lower() for keyword in HITL_TOOL_CALLS):
                write_tools.append(tool_call)

        if write_tools:
            write_tool_call_ids = [str(call["id"]) for call in write_tools]
            emit_event(
                "hitl.requested",
                {
                    "tool.count": len(write_tools),
                    "tool.names": [str(call["name"]) for call in write_tools],
                    "tool.call_ids": write_tool_call_ids,
                },
            )
            try:
                decision = interrupt(
                    {
                        "action": "approval_required",
                        "tool_calls": write_tools,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except GraphInterrupt:
                mark_current_span_status("interrupted")
                raise
            if decision.get("cancelled"):
                # Terminal cancellation (expired approval or an unrelated new
                # message): drop the pending write without executing it and end
                # the specialist subgraph instead of looping back to its LLM.
                reason = str(decision.get("feedback") or "superseded")
                emit_event(
                    "hitl.stale_cancelled",
                    {
                        "hitl.cancel.reason": reason,
                        "tool.count": len(write_tools),
                        "tool.call_ids": write_tool_call_ids,
                    },
                )
                rejection_messages = await _build_write_rejection_messages(
                    tool_calls,
                    set(write_tool_call_ids),
                    f"Pending write cancelled: {reason}. "
                    "It was neither approved nor saved.",
                    self.tools,
                )
                return {
                    "messages": rejection_messages,
                    "hitl_write_cancelled": True,
                }
            user_message = decision.get("user_message")
            if self.approval_resolver and isinstance(user_message, str):
                pending_tool_calls = [
                    {key: value for key, value in tool_call.items() if key != "type"}
                    for tool_call in write_tools
                ]
                approval = await self.approval_resolver.resolve(
                    user_message, pending_tool_calls
                )
                if approval.intent == "revise":
                    emit_event(
                        "hitl.resumed",
                        {
                            "decision": "revised",
                            "tool.count": len(write_tools),
                            "tool.call_ids": write_tool_call_ids,
                        },
                    )
                    rejection_messages = await _build_write_rejection_messages(
                        tool_calls,
                        set(write_tool_call_ids),
                        "Pending write superseded by user revision",
                        self.tools,
                    )
                    return {
                        "messages": rejection_messages
                        + [HumanMessage(content=approval.feedback)]
                    }
                decision = {
                    "approved": approval.intent == "approve",
                    "feedback": approval.feedback,
                }

            emit_event(
                "hitl.resumed",
                {
                    "decision": "approved" if decision.get("approved") else "rejected",
                    "tool.count": len(write_tools),
                    "tool.call_ids": write_tool_call_ids,
                },
            )

            if not decision.get("approved"):
                feedback = decision.get("feedback", "No feedback provided.")
                emit_event(
                    "hitl.cancelled",
                    {
                        "tool.count": len(write_tools),
                        "tool.call_ids": write_tool_call_ids,
                    },
                )
                rejection_messages = await _build_write_rejection_messages(
                    tool_calls,
                    set(write_tool_call_ids),
                    "User rejected the operation. " f"Feedback: {feedback}",
                    self.tools,
                )
                return {"messages": rejection_messages}

        executable_tool_calls = last_message.tool_calls
        if write_tools:
            executable_tool_calls = []
            for tool_call in last_message.tool_calls:
                executable_call = {
                    **tool_call,
                    "args": dict(tool_call["args"]),
                }
                if executable_call["name"] == "log_training_session":
                    executable_call["args"][
                        "operation_id"
                    ] = f"hitl:{executable_call['id']}"
                executable_tool_calls.append(executable_call)

        tasks = [
            _execute_single_tool_safely(call, self.tools)
            for call in executable_tool_calls
        ]

        # await all tool calls
        tool_outputs = await asyncio.gather(*tasks)
        if write_tools:
            emit_event(
                "hitl.executed",
                {
                    "tool.count": len(write_tools),
                    "tool.call_ids": write_tool_call_ids,
                    "tool.error_count": sum(
                        output.status == "error" for output in tool_outputs
                    ),
                },
            )
        return {"messages": list(tool_outputs)}
