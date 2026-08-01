import json
from datetime import date, datetime

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.tools import tool

from langgraph.graph import StateGraph, START
from langgraph.prebuilt import tools_condition

from agents.models import AgentState
from agents.sqlite_handler import (
    get_aggregated_training_between,
    get_aggregated_training_data,
    get_meal_records_between,
    get_meal_records_of_last_n_days,
)
from agents.llm_factory import create_chat_model, LLMConfig
from agents.utils import extract_text
from tools.safe_execution import SafeToolNode, _execute_llm_query_safely

INSTRUCTION_FOR_INSIGHTS = """
You are an Elite Strength & Conditioning Coach and Sports Data Analyst.
Your job is to analyze the user's recent training and dietary data to provide professional insights on their progress, recovery, and program design.

When the user asks for an analysis, you should call both `retrieve_recent_training` and `retrieve_recent_meals` to gather data (default to 21 days to spot trends). ONLY call these tools ONCE per request. Do NOT call them again if you already have the data.

ANALYTICAL FRAMEWORK:
1. Consistency & Volume: Are they training regularly? Look at `total_weight_volume` for strength and `total_reps` for bodyweight practices. Is there a logical progression or progressive overload?
2. Waveness & Intensity: Analyze the `avg_rpe` and `total_sets` across different days. A good program alternates High RPE / High Volume days with Low RPE recovery days. If they are constantly at RPE 8-10 without dipping to RPE 5-6, warn them about CNS fatigue and lack of waveness.
3. Recovery & Diet: Cross-reference their heavy training days with their meals. Did they eat enough carbohydrates and proteins to fuel their recovery? If meals are missing or inadequate during intense blocks, point this out.
4. Actionable Advice: Conclude with clear recommendations. Should they push harder? Deload? Eat more?

Be professional, encouraging, and highly data-driven. Do not simply list the numbers; synthesize them into a coherent story about their current physical trajectory.
"""


def make_insights_agent_graph(
    llm_config: LLMConfig,
    db_path: str,
    *,
    reporting_window: tuple[date, date] | None = None,
):
    llm = create_chat_model(llm_config)

    if reporting_window is None:

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

        training_tool = retrieve_recent_training
        meal_tool = retrieve_recent_meals
    else:
        start_date, end_date = reporting_window

        @tool("retrieve_recent_training")
        def retrieve_fixed_training():
            """Get training data for the fixed scheduled reporting window."""
            data = get_aggregated_training_between(start_date, end_date, db_path)
            return (
                json.dumps(data) if data else "No training data found for this period."
            )

        @tool("retrieve_recent_meals")
        def retrieve_fixed_meals():
            """Get meal data for the fixed scheduled reporting window."""
            data = get_meal_records_between(start_date, end_date, db_path)
            return (
                json.dumps(data) if data else "No meal records found for this period."
            )

        training_tool = retrieve_fixed_training
        meal_tool = retrieve_fixed_meals

    llm_with_tools = llm.bind_tools([training_tool, meal_tool])

    async def insights_node(state: AgentState):
        prompt_template = PromptTemplate.from_template(INSTRUCTION_FOR_INSIGHTS)
        system_prompt = prompt_template.format(
            current_time=datetime.now().date().isoformat()
        )
        if reporting_window is not None:
            system_prompt += (
                "\n\nThis is a scheduled weekly report. Use both tools exactly once "
                f"for the fixed reporting window {start_date.isoformat()} through "
                f"{end_date.isoformat()}, inclusive."
            )
        if summary := state.get("summary"):
            system_prompt += f"\n\n[Historical Conversation Summary]:\n{summary}"
        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        return await _execute_llm_query_safely(llm_with_tools, messages)

    builder = StateGraph(AgentState)
    builder.add_node("insights", insights_node)
    tool_node = SafeToolNode(tools=[training_tool, meal_tool])
    builder.add_node("tools", tool_node)  # type: ignore # type: ignore

    builder.add_edge(START, "insights")
    builder.add_conditional_edges("insights", tools_condition)
    builder.add_edge("tools", "insights")

    return builder.compile()


async def generate_weekly_insights(
    llm_config: LLMConfig, db_path: str, start_date: date, end_date: date
) -> str:
    """Generate a weekly summary from the supplied immutable date window."""
    app = make_insights_agent_graph(
        llm_config,
        db_path,
        reporting_window=(start_date, end_date),
    )
    response = await app.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "请基于本周训练和饮食记录，生成一份简洁、专业的中文周总结。"
                    )
                )
            ]
        }
    )
    required_tool_results = {
        "retrieve_recent_training": 0,
        "retrieve_recent_meals": 0,
    }
    for message in response["messages"]:
        if (
            isinstance(message, ToolMessage)
            and message.name in required_tool_results
            and message.status != "error"
        ):
            required_tool_results[message.name] += 1
    if any(count != 1 for count in required_tool_results.values()):
        raise RuntimeError("weekly insights generation failed")
    summary = extract_text(response["messages"][-1]).strip()
    if not summary or summary.startswith("[Error]"):
        raise RuntimeError("weekly insights generation failed")
    return summary
