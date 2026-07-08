from datetime import datetime

from agents.models import MealInfo, AgentState
from agents.sqlite_handler import add_meal_log
from agents.llm_factory import create_chat_model, LLMConfig
from agents.rag import create_recipe_rag_chain

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage
from langchain_core.prompts.prompt import PromptTemplate
from langgraph.graph import StateGraph, START

from langgraph.prebuilt import tools_condition
from tools.safe_execution import SafeToolNode, _execute_llm_query_safely

INSTRUCTION_FOR_RECORDING_MEALS = """
You are a nutrition and meal assistant.
Your job is to extract meal details and nutritional information from the user's messages and save them to the database.

When the user describes a meal or food they consumed, analyze their input semantically and extract the following:
- date: The date of the meal, use {current_time} if not specified.
- meal_type: The category of the meal (e.g., 'breakfast', 'lunch', 'dinner', 'snack', 'extra'). If not specified, infer based on context or leave as 'Extra'.
- items: A detailed description of the specific foods and drinks consumed.
- note: The user's full input as a descriptive note, capturing context (e.g., "eating out at an Italian restaurant") or how they felt.

Call the `log_meal` tool to save the information to db.

CRITICAL INSTRUCTIONS:
1. You have access to the `log_meal` tool. You MUST use this tool to save the data once you have extracted it.
2. If the user does not clearly specify what they ate (`food_items`), you must politely ask them for the food details BEFORE calling the tool.
3. If optional meal_type and items are missing, leave them null. DO NOT guess or hallucinate the value unless the user explicitly provides them.
4. If the user describes multiple distinct meals (e.g., "For breakfast I had eggs, and for lunch I had a salad"), you must call the `log_meal` tool multiple times in parallel to record each meal separately.
5. After successfully calling the tool, briefly acknowledge the logged meal.
"""

def make_meal_subagent_graph(llm_config: LLMConfig, db_path: str, vector_store):
    llm = create_chat_model(llm_config)

    @tool(args_schema=MealInfo)
    def log_meal(**kwargs):
        """Save meal record to database"""

        meal_info = MealInfo(**kwargs)
        add_meal_log(meal_info, db_path)
        return "Meal record saved successfully."

    @tool
    def advise_meals(question: str) -> str:
        """Use this tool to advise recipes based on the user's available ingredients and their cookbook."""

        rag_chain = create_recipe_rag_chain(vector_store, llm)
        result = rag_chain.invoke(question)
        return result.content

    llm_with_tools = llm.bind_tools([log_meal, advise_meals])

    async def log_meal_node(state: AgentState):
        prompt_template = PromptTemplate.from_template(INSTRUCTION_FOR_RECORDING_MEALS)
        system_prompt = prompt_template.format(
            current_time=datetime.now().isoformat()
        )
        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        return await _execute_llm_query_safely(llm_with_tools, messages)

    builder = StateGraph(AgentState)
    builder.add_node("log_meal", log_meal_node)
    tool_node = SafeToolNode(tools=[log_meal, advise_meals])
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "log_meal")
    builder.add_conditional_edges("log_meal", tools_condition)
    builder.add_edge("tools", "log_meal")

    return builder.compile()
