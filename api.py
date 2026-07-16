import os
import uuid
from typing import Any
from contextlib import asynccontextmanager

import aiosqlite

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agents.llm_factory import LLMConfig, create_chat_model
from langfuse.callback import CallbackHandler  # type: ignore
from agents.sqlite_handler import init_db
from agents.roles.supervisor import make_agent_graph
from agents.rag import get_or_create_vector_store
from agents.utils import extract_text


class ChatRequest(BaseModel):
    user_id: str
    message: str


class ChatResponse(BaseModel):
    response: str
    pending_tools: list[dict] | None = None


class ResumeRequest(BaseModel):
    user_id: str
    approved: bool


user_sessions: dict[str, str] = {}


def get_thread_id(user_id: str) -> str:
    if user_id not in user_sessions:
        user_sessions[user_id] = str(uuid.uuid4())
    return user_sessions[user_id]


@asynccontextmanager
async def startup_event(fastapi_app: FastAPI):
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

    print("Initializing Vector Store...")
    vector_store = get_or_create_vector_store(
        "~/Documents/LifeOS/下厨房/", os.path.join(".", "chroma.db")
    )

    print("Initializing Agent Graph...")
    # TODO make this configurable
    checkpointer_db = "checkpointer.db"
    async with aiosqlite.connect(checkpointer_db) as conn:
        checkpointer = AsyncSqliteSaver(conn)
        await checkpointer.setup()
        fastapi_app.state.agent = make_agent_graph(
            llm_config, db_path, vector_store, checkpointer=checkpointer
        )
        fastapi_app.state.llm_config = llm_config

        print("ChatFit API is ready.")

        yield


app = FastAPI(
    title="ChatFit API",
    description="API for ChatFit LangGraph Agent",
    lifespan=startup_event,
)


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


async def _classify_approval_intent(
    user_message: str, llm_config: LLMConfig
) -> tuple[bool, str]:
    llm = create_chat_model(llm_config)
    prompt = f"""
    The system is waiting for the user to approve a database write operation (like saving a training or meal record).
    The user's response is: "{user_message}"

    Determine if the user is approving the operation.
    - If they say yes, ok, go ahead, please do, or similar, it's an approval.
    - If they say no, wait, change something, or ask a completely different question, it's a rejection.

    Output ONLY a JSON object with this exact format:
    {{"approved": true/false, "feedback": "extract the user's message here as feedback"}}
    """
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        text = extract_text(response)
        import json

        text = text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        return data.get("approved", False), data.get("feedback", user_message)
    except Exception as e:
        print("Intent classification failed:", e)
        # default to rejection for safety if parsing fails
        return False, user_message


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest, request: Request):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    if not request.app.state.agent:
        raise HTTPException(status_code=500, detail="Agent application not initialized")

    # Use the Telegram user_id as the thread_id for LangGraph short-term memory separation
    thread_id = get_thread_id(req.user_id)
    langfuse_handler = CallbackHandler(session_id=thread_id, user_id=req.user_id)

    config = {"configurable": {"thread_id": thread_id}, "callbacks": [langfuse_handler]}

    # Check for pending interrupts
    state = await request.app.state.agent.aget_state(config)
    interrupts = []
    if state and state.tasks:
        for task in state.tasks:
            if task.interrupts:
                interrupts.extend(task.interrupts)

    if interrupts:
        # The graph is paused, treat this message as an approval/rejection
        is_approved, feedback = await _classify_approval_intent(
            req.message, request.app.state.llm_config
        )
        resume_data = {
            intr.id: {"approved": is_approved, "feedback": feedback}
            for intr in interrupts
        }
        action_command: Command[Any] | dict[str, Any] = Command(resume=resume_data)
    else:
        # Normal chat
        action_command = {"messages": [HumanMessage(content=req.message)]}

    final_response = ""

    # Stream the graph updates
    async for event in request.app.state.agent.astream(
        action_command, config=config, stream_mode="updates"
    ):
        # HITL interruptions
        if "__interrupt__" in event:
            interruption_data = event["__interrupt__"][0].value
            tool_calls = interruption_data.get("tool_calls", [])
            reply = await generate_conversational_approval(
                tool_calls, request.app.state.llm_config
            )

            # If the LLM also output some reasoning/text before the tool call, we can prepend it if it's helpful,
            # but usually it assumes the tool succeeded. It's safer to just use the generated approval reply.
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
                        # We accumulate the response texts from the agents
                        final_response += text_content + "\n\n"

    return ChatResponse(response=final_response.strip())


@app.post("/clear")
def clear_endpoint(req: ChatRequest):
    user_sessions[req.user_id] = str(uuid.uuid4())
    return ChatResponse(
        response="Conversation context cleared! You are starting fresh."
    )
