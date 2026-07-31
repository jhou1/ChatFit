import asyncio
import json
from typing import Any, Literal, Protocol, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from agents.llm_factory import LLMConfig, create_chat_model
from agents.observability import (
    emit_event,
    error_attributes,
    mark_current_span_status,
    observe_span,
)
from agents.utils import extract_text

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
        self.llm = create_chat_model(llm_config)

    async def resolve(
        self, user_message: str, pending_tool_calls: list[dict]
    ) -> ApprovalDecision:
        instruction = (
            "Classify a reply to a pending database-write approval. Return "
            "approve only when it purely approves the exact pending data. "
            "Return revise when it adds, corrects, removes, or replaces any "
            "business data, even if it also says approve. Return reject when "
            "it declines the write. Output only JSON with one intent field."
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
            payload = json.loads(extract_text(response["messages"]))
            intent = ApprovalIntentModel.model_validate(payload).intent
        except (ValueError, TypeError, ValidationError):
            intent = "reject"
        return ApprovalDecision(intent=intent, feedback=user_message)


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
                    {"action": "approval_required", "tool_calls": write_tools}
                )
            except GraphInterrupt:
                mark_current_span_status("interrupted")
                raise
            emit_event(
                "hitl.resumed",
                {
                    "decision": (
                        "approved" if decision.get("approved") else "rejected"
                    ),
                    "tool.count": len(write_tools),
                    "tool.call_ids": write_tool_call_ids,
                },
            )

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
                    write_tool_call_id_set = set(write_tool_call_ids)
                    read_tool_calls = [
                        tool_call
                        for tool_call in tool_calls
                        if str(tool_call["id"]) not in write_tool_call_id_set
                    ]
                    read_outputs = await asyncio.gather(
                        *[
                            _execute_single_tool_safely(tool_call, self.tools)
                            for tool_call in read_tool_calls
                        ]
                    )
                    read_outputs_by_id = {
                        str(output.tool_call_id): output for output in read_outputs
                    }
                    return {
                        "messages": [
                            (
                                ToolMessage(
                                    name=tool_call["name"],
                                    tool_call_id=tool_call["id"],
                                    content=(
                                        "Pending write superseded by user revision"
                                    ),
                                    status="error",
                                )
                                if str(tool_call["id"]) in write_tool_call_id_set
                                else read_outputs_by_id[str(tool_call["id"])]
                            )
                            for tool_call in tool_calls
                        ]
                        + [HumanMessage(content=approval.feedback)]
                    }
                decision = {
                    "approved": approval.intent == "approve",
                    "feedback": approval.feedback,
                }

            if not decision.get("approved"):
                feedback = decision.get("feedback", "No feedback provided.")
                emit_event(
                    "hitl.cancelled",
                    {
                        "tool.count": len(write_tools),
                        "tool.call_ids": write_tool_call_ids,
                    },
                )
                write_tool_call_id_set = set(write_tool_call_ids)
                read_tool_calls = [
                    tool_call
                    for tool_call in tool_calls
                    if str(tool_call["id"]) not in write_tool_call_id_set
                ]
                read_outputs = await asyncio.gather(
                    *[
                        _execute_single_tool_safely(tool_call, self.tools)
                        for tool_call in read_tool_calls
                    ]
                )
                read_outputs_by_id = {
                    str(output.tool_call_id): output for output in read_outputs
                }
                return {
                    "messages": [
                        (
                            ToolMessage(
                                name=tool_call["name"],
                                tool_call_id=tool_call["id"],
                                content=(
                                    "User rejected the operation. "
                                    f"Feedback: {feedback}"
                                ),
                                status="error",
                            )
                            if str(tool_call["id"]) in write_tool_call_id_set
                            else read_outputs_by_id[str(tool_call["id"])]
                        )
                        for tool_call in tool_calls
                    ]
                }

        tasks = [
            _execute_single_tool_safely(call, self.tools)
            for call in last_message.tool_calls
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
