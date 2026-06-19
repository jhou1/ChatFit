from schema.training import TrainingSession
from storage.db import add_training_session
from llm_factory.llm_factory import create_chat_model, LLMConfig

from datetime import datetime
from typing import TypedDict, Annotated

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

SYSTEM_PROMPT = """You are an fitness and training recording assistant. 
Your goal is to extract training/workout details from the user's messages and save them to the database.

When the user describes a training/workout session, analyze their input semantically and extract the following:
- date: The date of the workout. If not specified, use {current_date}.
- practice_name: The main exercise or activity (e.g., 'Running', 'Weightlifting').
- warm_up / cool_down: Any specific warm up or cool down activities mentioned.
- distance: Distance in km 
- duration: Duration in minutes.
- reps / sets / weight: For strength training.
- rpe: Rate of Perceived Exertion (1-10 scale).
- note: The user's full input as a descriptive note, capturing the overall vibe and any gear used.

Call the `save_training_session` tool to save the training sessions to db.

CRITICAL INSTRUCTIONS:
1. You have access to the `save_training_session` tool. You MUST use this tool to save the data once you have extracted it.
2. If the user does not provide the `practice_name`, you must politely ask them what exercise they did BEFORE calling the tool.
3. If optional fields (like rpe, distance, etc.) are missing, leave them null. Do not guess them.
4. If user trained multiple exercise, record each item respectively.
5. After successfully calling the tool, briefly congratulate the user on their workout.
"""

# TODO
def expand_acronym():
    """ explain the acronym """
    pass

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

# factory function to inject dependencies (llm_config, db_path)
def make_training_subgraph(llm_config: LLMConfig, db_path: str):

    # Tools
    @tool(args_schema=TrainingSession)
    def save_training_session(**kwargs):
        """Save the user training log to db."""
        training_session = TrainingSession(**kwargs)
        add_training_session(training_session, db_path)
        return "Training log saved successfully!"

    # LLM and its http client are created only once when subgraph is compiled
    llm = create_chat_model(llm_config)
    llm_with_tools = llm.bind_tools([save_training_session])

    # Nodes
    def log_training_node(state: AgentState):
        formatted_system_prompt = SYSTEM_PROMPT.format(
            current_date=datetime.now().date().isoformat()
        )
        system_msg = SystemMessage(content=formatted_system_prompt)
        response = llm_with_tools.invoke([system_msg] + state["messages"])

        return {"messages": [response]}

    builder = StateGraph(AgentState)
    builder.add_node("log_training_node", log_training_node)

    tool_node = ToolNode(tools=[save_training_session])
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "log_training_node")
    builder.add_conditional_edges("log_training_node", tools_condition)
    builder.add_edge("tools", "log_training_node")

    return builder.compile()
