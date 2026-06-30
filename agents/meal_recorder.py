from datetime import datetime

from models import MealInfo, AgentState
from utils.db import add_meal_record
from utils.llm_factory import create_chat_model, LLMConfig
from prompts import MEAL_RECORDER_INSTRUCTION
from rag import create_recipe_rag_chain

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage
from langchain_core.prompts.prompt import PromptTemplate
from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition

def make_record_meal_graph(llm_config: LLMConfig, db_path: str, vector_store):
    llm = create_chat_model(llm_config)

    @tool(args_schema=MealInfo)
    def save_meal_record(**kwargs):
        """Save meal record to database"""

        meal_record = MealInfo(**kwargs)
        add_meal_record(meal_record, db_path)
        return "Meal record saved successfully."

    @tool
    def advise_recipe(question: str) -> str:
        """Use this tool to advise recipes based on the user's available ingredients and their cookbook."""

        rag_chain = create_recipe_rag_chain(vector_store, llm)
        result = rag_chain.invoke(question)
        return result.content

    llm_with_tools = llm.bind_tools([save_meal_record, advise_recipe])

    def record_meal(state: AgentState):
        prompt_template = PromptTemplate.from_template(MEAL_RECORDER_INSTRUCTION)
        system_prompt = prompt_template.format(
            current_time=datetime.now().isoformat()
        )

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"messages": response}

    builder = StateGraph(AgentState)
    builder.add_node("record_meal", record_meal)
    tool_node = ToolNode(tools=[save_meal_record, advise_recipe])
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "record_meal")
    builder.add_conditional_edges("record_meal", tools_condition)
    builder.add_edge("tools", "record_meal")

    return builder.compile()
