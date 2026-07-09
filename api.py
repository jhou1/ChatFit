import os
import uuid
from contextlib import asynccontextmanager

import aiosqlite

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agents.llm_factory import LLMConfig
from agents.sqlite_handler import init_db
from agents.roles.supervisor import make_agent_graph
from agents.rag import get_or_create_vector_store

class ChatRequest(BaseModel):
    user_id: str
    message: str

class ChatResponse(BaseModel):
    response: str
    pending_tools: list[dict] | None = None

class ResumeRequest(BaseModel):
    user_id: str
    approved: bool

user_sessions = {}

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
        max_tokens=2048,
        kwargs=kwargs
    )

    db_path = os.path.expanduser("~/.iron/iron.db")

    if not os.path.exists(db_path):
        init_db(db_path)

    print("Initializing Vector Store...")
    vector_store = get_or_create_vector_store("~/Documents/LifeOS/下厨房/", os.path.join(".", "chroma.db"))

    print("Initializing Agent Graph...")
    # TODO make this configurable
    checkpointer_db = "checkpointer.db"
    async with aiosqlite.connect(checkpointer_db) as conn:
        checkpointer = AsyncSqliteSaver(conn)
        await checkpointer.setup()
        fastapi_app.state.agent = make_agent_graph(llm_config, db_path, vector_store, checkpointer=checkpointer)

        print("ChatFit API is ready.")

        yield

app = FastAPI(
    title="ChatFit API",
    description="API for ChatFit LangGraph Agent",
    lifespan=startup_event
)

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest, request: Request):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    if not request.app.state.agent:
        raise HTTPException(status_code=500, detail="Agent application not initialized")

    # Use the Telegram user_id as the thread_id for LangGraph short-term memory separation
    thread_id = get_thread_id(req.user_id)
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {"messages": [HumanMessage(content=req.message)]}

    final_response = ""

    # Stream the graph updates
    async for event in request.app.state.agent.astream(initial_state, config=config, stream_mode="updates"):
        # HITL interruptions
        if "__interrupt__" in event:
            interruption_data = event["__interrupt__"][0].value
            return ChatResponse(
                response="[SYSTEM_APPROVAL]",
                pending_tools=interruption_data["tool_calls"]
            )
        for node_name, node_output in event.items():
            if node_name in ["training", "meal", "assistant_selector", "chatter"]:
                new_messages = node_output.get("messages", [])
                if new_messages:
                    last_message = new_messages[-1]

                    # Handle Gemini's list-based content (extract text parts)
                    if isinstance(last_message.content, list):
                        text_content = "".join(
                            part.get("text", "") for part in last_message.content
                            if isinstance(part, dict) and "text" in part
                        )
                    else:
                        text_content = str(last_message.content)

                    if text_content.strip():
                        # We accumulate the response texts from the agents
                        final_response += text_content + "\n\n"

    return ChatResponse(response=final_response.strip())

@app.post("/clear")
def clear_endpoint(req: ChatRequest):
    user_sessions[req.user_id] = str(uuid.uuid4())
    return ChatResponse(response="Conversation context cleared! You are starting fresh.")

@app.post("/resume", response_model=ChatResponse)
async def resume_checkpoint(req: ResumeRequest, request: Request):
    thread_id = get_thread_id(req.user_id)
    config = {"configurable": {"thread_id": thread_id}}
    resume_command = Command(resume={"approved": req.approved})
    final_response = ""
    async for event in request.app.state.agent.astream(resume_command, config=config, stream_mode="updates"):
        for node_name, node_output in event.items():
            if node_name in ["training", "meal", "assistant_selector", "chatter"]:
                new_messages = node_output.get("messages", [])
                if new_messages:
                    last_message = new_messages[-1]

                    # Handle Gemini's list-based content (extract text parts)
                    if isinstance(last_message.content, list):
                        text_content = "".join(
                            part.get("text", "") for part in last_message.content
                            if isinstance(part, dict) and "text" in part
                        )
                    else:
                        text_content = str(last_message.content)

                    if text_content.strip():
                        # We accumulate the response texts from the agents
                        final_response += text_content + "\n\n"

    return ChatResponse(response=final_response.strip())
