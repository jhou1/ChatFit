# the trianing agent
from schema.training import TrainingLog
from storage.db import add_training_log
from llm_factory.llm_factory import create_chat_model, LLMConfig

from datetime import datetime
from typing import TypedDict, Annotated

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

SYSTEM_PROMPT = """You are an expert fitness and training recording assistant. 
Your primary goal is to extract workout details from the user's messages and save them to the database.

When the user describes a training/workout session, analyze their input semantically and extract the following:
- date: The date of the workout. If not specified, use `get_current_date` tool to get the date.
- practice_name: The main exercise or activity (e.g., 'Running', 'Weightlifting').
- warm_up / cool_down: Any specific warm up or cool down activities mentioned.
- distance: Distance in km 
- duration: Duration in minutes.
- reps / sets / weight: For strength training.
- rpe: Rate of Perceived Exertion (1-10 scale).
- note: The user's full input as a descriptive note, capturing the overall vibe and any gear used.

CRITICAL INSTRUCTIONS:
1. You have access to the `save_training_log` tool. You MUST use this tool to save the data once you have extracted it.
2. If the user does not provide the `practice_name`, you must politely ask them what exercise they did BEFORE calling the tool.
3. If optional fields (like rpe, distance, etc.) are missing, leave them null. Do not guess them.
4. After successfully calling the tool, briefly congratulate the user on their workout.
"""

@tool
def get_current_date():
    """Get current date"""
    return datetime.now().date()

@tool(args_schema=TrainingLog)
def save_training_log(log, db_path):
    """Save the user training log to db."""
    add_training_log(log, db_path)

# TODO
def expand_acronym():
    """ explain the acronym """
    pass

class AgentState(TypedDict):
    messages: Annotated[list: add_messages]

def log_training_node(state: AgentState):
    llm = create_chat_model(LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        temperature=0,
    ))
    llm_with_tools = llm.bind_tools([save_training_log])
    system_msg = SystemMessage(content=SYSTEM_PROMPT)
    response = llm_with_tools.invoke([system_msg] + state["messages"])

    return {"messages": response}

builder = StateGraph(AgentState)
builder.add_node("log_training_node", log_training_node)
tool_node = ToolNode(tools=[get_current_date, save_training_log])
builder.add_node("tools", tool_node)

builder.add_edge(START, "log_training_node")
builder.add_conditional_edges("log_training_node", tools_condition)
builder.add_edge("tools", "add_training_log")

app = builder.compile
