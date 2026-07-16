import json
from datetime import datetime

from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage

from langgraph.graph import StateGraph, START
from langgraph.prebuilt import tools_condition

from agents.models import AgentState
from agents.sqlite_handler import (
    get_aggregated_training_data,
    get_meal_records_of_last_n_days,
)
from agents.llm_factory import create_chat_model, LLMConfig
from tools.safe_execution import SafeToolNode, _execute_llm_query_safely

INSTRUCTION_FOR_INSIGHTS = """
You are an Elite Strength & Conditioning Coach and Sports Data Analyst.
Your job is to analyze the user's recent training and dietary data to provide professional insights on their progress, recovery, and program design.

When the user asks for an analysis, you should call both `retrieve_recent_training` and `retrieve_recent_meals` to gather data (default to 21 days to spot trends).

ANALYTICAL FRAMEWORK:
1. Consistency & Volume: Are they training regularly? Look at `total_weight_volume` for strength and `total_reps` for bodyweight practices. Is there a logical progression or progressive overload?
2. Waveness & Intensity: Analyze the `avg_rpe` and `total_sets` across different days. A good program alternates High RPE / High Volume days with Low RPE recovery days. If they are constantly at RPE 8-10 without dipping to RPE 5-6, warn them about CNS fatigue and lack of waveness.
3. Recovery & Diet: Cross-reference their heavy training days with their meals. Did they eat enough carbohydrates and proteins to fuel their recovery? If meals are missing or inadequate during intense blocks, point this out.
4. Actionable Advice: Conclude with clear recommendations. Should they push harder? Deload? Eat more?

Be professional, encouraging, and highly data-driven. Do not simply list the numbers; synthesize them into a coherent story about their current physical trajectory.
"""


def make_insights_agent_graph(llm_config: LLMConfig, db_path: str):
    llm = create_chat_model(llm_config)

    @tool
    def retrieve_recent_training(days: int = 21):
        """Get aggregated training volumes, sets, and average RPE for the last N days (default 21)."""
        data = get_aggregated_training_data(days, db_path)
        if len(data) == 0:
            return "No training data found for this period."
        return json.dumps(data)

    @tool
    def retrieve_recent_meals(days: int = 21):
        """Get meal records for the last N days (default 21)."""
        data = get_meal_records_of_last_n_days(days, db_path)
        if len(data) == 0:
            return "No meal records found for this period."
        return json.dumps(data)

    llm_with_tools = llm.bind_tools([retrieve_recent_training, retrieve_recent_meals])

    async def insights_node(state: AgentState):
        prompt_template = PromptTemplate.from_template(INSTRUCTION_FOR_INSIGHTS)
        system_prompt = prompt_template.format(current_time=datetime.now().isoformat())
        if state.get("summary"):
            system_prompt += (
                f"\n\n[Historical Conversation Summary]:\n{state['summary']}"
            )
        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        return await _execute_llm_query_safely(llm_with_tools, messages)

    builder = StateGraph(AgentState)
    builder.add_node("insights", insights_node)
    tool_node = SafeToolNode(tools=[retrieve_recent_training, retrieve_recent_meals])
    builder.add_node("tools", tool_node)  # type: ignore # type: ignore

    builder.add_edge(START, "insights")
    builder.add_conditional_edges("insights", tools_condition)
    builder.add_edge("tools", "insights")

    return builder.compile()
