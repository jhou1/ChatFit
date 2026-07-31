import os
import uuid
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from agents.checkpointing import ObservedAsyncSqliteSaver
from agents.llm_factory import LLMConfig, create_chat_model
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler  # type: ignore
from agents.sqlite_handler import init_db
from agents.roles.supervisor import make_agent_graph
from agents.rag import get_or_create_vector_store
from agents.roles.insights import generate_weekly_insights
from agents.utils import extract_text
from agents.observability import (
    content_attributes,
    emit_event,
    hash_user_identifier,
    mark_current_span_status,
    mark_trace_status,
    observe_span,
    start_trace,
)
from proactive_reviews import build_proactive_review, today_in_shanghai


class ChatRequest(BaseModel):
    user_id: str
    message: str


class ChatResponse(BaseModel):
    response: str
    pending_tools: list[dict] | None = None


class ProactiveReviewResponse(BaseModel):
    should_send: bool
    message: str | None


user_sessions: dict[str, str] = {}
logger = logging.getLogger(__name__)
CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def get_thread_id(user_id: str) -> str:
    if user_id not in user_sessions:
        user_sessions[user_id] = str(uuid.uuid4())
    return user_sessions[user_id]


def create_langfuse_callback(trace_id: str) -> Any | None:
    """Create optional tracing without making chat availability depend on it."""

    try:
        # Langfuse v4 reads host and credentials from LANGFUSE_* environment
        # variables. Passing the removed `host` keyword raises TypeError.
        return CallbackHandler(trace_context={"trace_id": trace_id})
    except Exception:
        logger.warning(
            "Langfuse callback initialization failed; tracing is disabled",
            exc_info=True,
        )
        emit_event(
            "observability.langfuse_degraded",
            {"reason": "callback_initialization_failed"},
        )
        return None


def mask_langfuse_content(*, data: Any, **kwargs: Any) -> Any:
    """Redact prompt/output content unless a deployment explicitly opts in."""

    capture_content = os.environ.get("LANGFUSE_CAPTURE_CONTENT", "false").lower()
    if capture_content == "true":
        return data
    return "[REDACTED]"


def create_langfuse_client() -> Langfuse | None:
    """Initialize the shared masked Langfuse client without affecting startup."""

    try:
        return Langfuse(mask=mask_langfuse_content)
    except Exception:
        logger.warning(
            "Langfuse client initialization failed; tracing is disabled",
            exc_info=True,
        )
        return None


def get_correlation_id(request: Request, header_name: str) -> str | None:
    """Accept only bounded, display-safe correlation identifiers."""

    value = request.headers.get(header_name)
    if value and CORRELATION_ID_PATTERN.fullmatch(value):
        return value
    return None


def get_checkpointer_db_path() -> str:
    """Resolve a writable SQLite file and reject directory-shaped bind mounts."""

    db_path = Path(os.environ.get("CHECKPOINTER_DB_PATH", "checkpointer.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists() and not db_path.is_file():
        raise RuntimeError(
            f"CHECKPOINTER_DB_PATH must be a file path, not a directory: {db_path}"
        )
    return str(db_path)


@asynccontextmanager
async def startup_event(fastapi_app: FastAPI):
    langfuse_client = create_langfuse_client()
    fastapi_app.state.langfuse_client = langfuse_client

    llm_proxy = os.environ.get("LLM_PROXY", None)
    kwargs = {}
    if llm_proxy:
        kwargs["client_args"] = {"proxy": llm_proxy}

    llm_config = LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        temperature=0.5,
        max_tokens=8192,
        kwargs=kwargs,
    )

    db_path = os.path.expanduser("~/.iron/iron.db")

    if not os.path.exists(db_path):
        init_db(db_path)
    fastapi_app.state.db_path = db_path

    print("Initializing Vector Store...")
    vector_store = get_or_create_vector_store(
        "~/Documents/LifeOS/下厨房/", os.path.join(".", "chroma.db")
    )

    print("Initializing Agent Graph...")
    # TODO make this configurable
    checkpointer_db = get_checkpointer_db_path()
    async with aiosqlite.connect(checkpointer_db) as conn:
        checkpointer = ObservedAsyncSqliteSaver(conn)
        await checkpointer.setup()
        fastapi_app.state.agent = make_agent_graph(
            llm_config, db_path, vector_store, checkpointer=checkpointer
        )
        fastapi_app.state.llm_config = llm_config

        print("ChatFit API is ready.")

        yield

    if langfuse_client is not None:
        try:
            langfuse_client.flush()
        except Exception:
            logger.warning("Langfuse flush failed during shutdown", exc_info=True)


app = FastAPI(
    title="ChatFit API",
    description="API for ChatFit LangGraph Agent",
    lifespan=startup_event,
)


@app.middleware("http")
async def add_chat_correlation_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach correlation IDs to successful and handled-error chat responses."""

    if request.url.path != "/chat":
        return await call_next(request)

    request.state.request_id = (
        get_correlation_id(request, "X-Request-ID") or uuid.uuid4().hex
    )
    request.state.trace_id = uuid.uuid4().hex
    downstream_response = await call_next(request)
    downstream_response.headers["X-Request-ID"] = request.state.request_id
    downstream_response.headers["X-Trace-ID"] = request.state.trace_id
    return downstream_response


async def generate_conversational_approval(
    tool_calls: list, llm_config: LLMConfig
) -> str:
    llm = create_chat_model(llm_config)
    import json

    tools_str = json.dumps(tool_calls, indent=2, ensure_ascii=False)

    prompt = f"""
    The assistant is about to execute the following database write operations:
    {tools_str}

    Generate a friendly, conversational message (in Chinese) telling the user what you are about to save, and asking for their approval.
    Be concise but clear about the data being saved. Do NOT use technical terms like "JSON" or "tool call".
    """
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        text = extract_text(response)
        return text.strip()
    except Exception as e:
        print("Conversational approval generation failed:", e)
        return "⚠️ I'm about to write save the records to database, is it OK?"


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest, request: Request, response: Response):
    # Use the Telegram user_id to resolve the current LangGraph session.
    thread_id = get_thread_id(req.user_id)
    request_id = request.state.request_id
    trace_id = request.state.trace_id
    run_id = get_correlation_id(request, "X-Evaluation-Run-ID")
    case_id = get_correlation_id(request, "X-Evaluation-Case-ID")
    user_key = hash_user_identifier(req.user_id)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Trace-ID"] = trace_id

    with start_trace(
        "chat.request",
        request_id=request_id,
        session_id=thread_id,
        user_key=user_key,
        trace_id=trace_id,
        run_id=run_id,
        case_id=case_id,
        attributes={
            "http.route": "/chat",
            "http.method": "POST",
            **content_attributes(req.message, "request.message"),
        },
    ):
        if not req.message.strip():
            raise HTTPException(status_code=400, detail="Empty message")

        if not request.app.state.agent:
            raise HTTPException(
                status_code=500, detail="Agent application not initialized"
            )

        langfuse_handler = create_langfuse_callback(trace_id)
        config = {
            "configurable": {"thread_id": thread_id},
            "callbacks": [langfuse_handler] if langfuse_handler is not None else [],
            "metadata": {
                "trace_id": trace_id,
                "request_id": request_id,
                "session_id": thread_id,
                "user_key": user_key,
                "run_id": run_id,
                "case_id": case_id,
            },
        }

        # Check for pending interrupts
        state = await request.app.state.agent.aget_state(config)
        interrupts = []
        if state and state.tasks:
            for task in state.tasks:
                if task.interrupts:
                    interrupts.extend(task.interrupts)

        if interrupts:
            emit_event(
                "hitl.reply_received",
                {
                    "interrupt.count": len(interrupts),
                    "interrupt.ids": [str(intr.id) for intr in interrupts],
                },
            )
            resume_data = {
                intr.id: {"user_message": req.message} for intr in interrupts
            }
            action_command: Command[Any] | dict[str, Any] = Command(resume=resume_data)
        else:
            action_command = {"messages": [HumanMessage(content=req.message)]}

        final_response = ""

        with observe_span("graph.run"):
            async for event in request.app.state.agent.astream(
                action_command, config=config, stream_mode="updates"
            ):
                emit_event(
                    "graph.update",
                    {"graph.nodes": sorted(str(name) for name in event)},
                )
                if "__interrupt__" in event:
                    event_interrupts = event["__interrupt__"]
                    tool_calls = [
                        tool_call
                        for interruption in event_interrupts
                        for tool_call in interruption.value.get("tool_calls", [])
                    ]
                    emit_event(
                        "hitl.requested",
                        {
                            "interrupt.ids": [
                                str(interruption.id)
                                for interruption in event_interrupts
                            ],
                            "tool.count": len(tool_calls),
                            "tool.names": [
                                str(tool_call.get("name", "unknown"))
                                for tool_call in tool_calls
                            ],
                        },
                    )
                    mark_current_span_status("interrupted")
                    mark_trace_status("interrupted")
                    reply = await generate_conversational_approval(
                        tool_calls, request.app.state.llm_config
                    )
                    return ChatResponse(response=reply, pending_tools=None)
                for node_name, node_output in event.items():
                    if node_name in [
                        "training",
                        "meal",
                        "insights",
                        "assistant_selector",
                        "chatter",
                    ]:
                        new_messages = node_output.get("messages", [])
                        if new_messages:
                            last_message = new_messages[-1]
                            text_content = extract_text(last_message)

                            if text_content.strip():
                                final_response += text_content + "\n\n"

        emit_event(
            "chat.response.created",
            content_attributes(final_response.strip(), "response.message"),
        )
        return ChatResponse(response=final_response.strip())


@app.post("/proactive-review", response_model=ProactiveReviewResponse)
async def proactive_review_endpoint(request: Request) -> ProactiveReviewResponse:
    async def weekly_summary(start_date: date, end_date: date) -> str:
        return await generate_weekly_insights(
            request.app.state.llm_config,
            request.app.state.db_path,
            start_date,
            end_date,
        )

    result = await build_proactive_review(
        today_in_shanghai(), request.app.state.db_path, weekly_summary
    )
    return ProactiveReviewResponse(
        should_send=result.should_send,
        message=result.message,
    )


@app.post("/clear")
def clear_endpoint(req: ChatRequest):
    user_sessions[req.user_id] = str(uuid.uuid4())
    return ChatResponse(
        response="Conversation context cleared! You are starting fresh."
    )
