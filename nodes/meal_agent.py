from schema.meal import MealRecord
from storage.db import add_meal_record
from llm_factory.llm_factory import create_chat_model, LLMConfig

from datetime import datetime
from typing import TypedDict, Annotated

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

SYSTEM_PROMPT="""You are a nutrition and meal recording assistant. 
Your goal is to extract meal details and nutritional information from the user's messages and save them to the database.

When the user describes a meal or food they consumed, analyze their input semantically and extract the following:
- date: The date of the meal, use {current_date} if not specified.
- meal_type: The category of the meal (e.g., 'breakfast', 'lunch', 'dinner', 'snack', 'extra'). If not specified, infer based on context or leave as 'Extra'.
- items: A detailed description of the specific foods and drinks consumed.
- note: The user's full input as a descriptive note, capturing context (e.g., "eating out at an Italian restaurant") or how they felt.

Call the `save_meal_record` tool to save the information to db.

CRITICAL INSTRUCTIONS:
1. You have access to the `save_meal_record` tool. You MUST use this tool to save the data once you have extracted it.
2. If the user does not clearly specify what they ate (`food_items`), you must politely ask them for the food details BEFORE calling the tool.
3. If optional meal_type and items are missing, leave them null. DO NOT guess or hallucinate the value unless the user explicitly provides them.
4. If the user describes multiple distinct meals (e.g., "For breakfast I had eggs, and for lunch I had a salad"), you must call the `save_meal_record` tool multiple times in parallel to record each meal separately.
5. After successfully calling the tool, briefly acknowledge the logged meal.
"""

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

def make_meal_subgraph(llm_config: LLMConfig, db_path: str):

    @tool(args_schema=MealRecord)
    def save_meal_record(**kwargs):
        """Save meal record to database"""
        meal_record = MealRecord(**kwargs)
        add_meal_record(meal_record, db_path)
        return "Meal record saved successfully."

    def log_meal_node(state: AgentState):
        llm = create_chat_model(llm_config)
        llm_with_tools = llm.bind_tools([save_meal_record])
        formatted_system_prompt = SYSTEM_PROMPT.format(
            current_date=datetime.now().date().isoformat()
        )
        system_msg = SystemMessage(content=formatted_system_prompt)
        response = llm_with_tools.invoke([system_msg] + state["messages"])

        return {"messages": [response]}

    builder = StateGraph(AgentState)
    builder.add_node("log_meal_node", log_meal_node)

    tool_node = ToolNode(tools=[save_meal_record])
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "log_meal_node")
    builder.add_conditional_edges("log_meal_node", tools_condition)
    builder.add_edge("tools", "log_meal_node")

    return builder.compile()
