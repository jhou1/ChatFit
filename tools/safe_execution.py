import asyncio
from typing import Sequence, Any

from langchain_core.messages import ToolCall, ToolMessage, AIMessage, tool
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt


MAX_RETRIES = 3
MAX_TOOL_TOKENS = 5000
LLM_TIMEOUT_SECONDS = 30.0
MIN_TOOL_EXECUTION_TOKENS = 50
MAX_OUTPUT_TOKENS = 500
TRUNCATE_WARNINGS = "\n[OUTPUT TRUNCATED - the tool returned more data than can be processed. Please ask a more specific question]"
HITL_TIMEOUT_SECONDS = 300.0 # 5 minutes for human-in-the-loop timeout
HITL_TOOL_CALLS = ["log_training_session", "log_meal"]


def _is_transient_tool_error(error: Exception) -> bool:
    """Return True if a tool execution is likely transient"""
    if isinstance(error, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    if isinstance(error, OSError):
        return True
    error_text = str(error).lower()
    return any(
        token in error_text for token in ("timeout", "temporary", "connection reset", "connection closed")
    )

def _is_rate_limit_tool_error(error: Exception) -> bool:
    """Return True if a tool execution indicates rate limit"""
    error_text = str(error).lower()
    return any(
        token in error_text for token in ("rate limit", "429", "too many requests")
    )


async def _execute_single_tool_safely(tool_call: dict, tool_list: list) -> ToolMessage:
    """Safely execute tool call, catch exception, and return mild message"""
    tool_name = tool_call["name"]
    tool_args = tool_call["args"]
    tool_id = tool_call["id"]

    # tool must be an instance of tool_list
    tool_instance = next((t for t in tool_list if t.name == tool_name), None)
    if not tool_instance:
        return ToolMessage(content=f"[Error] Tool tool_name not found.", tool_call_id=tool_id, status="error")

    # tool call with retry
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            result = await asyncio.to_thread(tool_instance.invoke, tool_args)
            result_str = str(result)
            max_output_length = MAX_OUTPUT_TOKENS * 4
            if len(result_str) > max_output_length:
                result_str = result_str[:max_output_length] + TRUNCATE_WARNINGS

            return ToolMessage(content=result_str, tool_call_id=tool_id)

        except Exception as e:
            last_error = e
            if _is_transient_tool_error(e):
                await asyncio.sleep(1 * (2 ** attempt))
                continue
            if _is_rate_limit_tool_error(e):
                await asyncio.sleep(2 * (2 ** attempt))
                continue
            break

    error_msg = f"[Error] Tool execution failed after retries: {str(last_error)}"
    return ToolMessage(content=error_msg, tool_call_id=tool_id, status="error")

async def _execute_llm_query_safely(llm_with_tools, messages) -> dict:
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
                response = await llm_with_tools.ainvoke(messages)
                return {"messages": response}
        except Exception as e:
            last_error = e
            if _is_transient_tool_error(e):
                await asyncio.sleep(1 * (2 ** attempt))
                continue
            if _is_rate_limit_tool_error(e):
                await asyncio.sleep(2 * (2 ** attempt))
                continue
            break

    error_text = f"[Error] LLM request timeout exceeded ({LLM_TIMEOUT_SECONDS}s): {str(last_error)}"
    response = AIMessage(content=error_text)
    return {"messages": response}

class SafeToolNode:
    """Callable, wraps safe tool call and can be used like a LangGraph ToolNode"""

    # using Sequence to accept list or tuple of tools
    def __init__(self, tools: Sequence[Any]):
        self.tools = tools

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
            decision = interrupt({
                "action": "approval_required",
                "tool_calls": write_tools
            })

            for tool_call in write_tools:
                if not decision.get("approved"):
                    return {"messages": [ToolMessage(
                        tool_call_id = tool_call["id"],
                        content="User rejected the operation.",
                        status="error"
                    )]}

        tasks = [_execute_single_tool_safely(call, self.tools) for call in last_message.tool_calls]

        # await all tool calls
        tool_outputs = await asyncio.gather(*tasks)
        return {"messages": list(tool_outputs)}
