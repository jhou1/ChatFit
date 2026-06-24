from datetime import datetime

from models import TrainingSessionInfo, AgentState
from utils.db import add_training_session
from utils.llm_factory import create_chat_model, LLMConfig
from prompts import TRAINING_RECORDER_INSTRUCTION

from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage

from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition

def make_record_training_graph(llm_config: LLMConfig, db_path: str):

    @tool(args_schema=TrainingSessionInfo)
    def save_training_session(**kwargs):
        """Save the user training log to db."""

        training_session = TrainingSessionInfo(**kwargs)
        add_training_session(training_session, db_path)
        return "Training log saved successfully!"

    llm = create_chat_model(llm_config)
    llm_with_tools = llm.bind_tools([save_training_session])

    def record_training(state: AgentState):
        prompt_template = PromptTemplate.from_template(TRAINING_RECORDER_INSTRUCTION)
        system_prompt = prompt_template.format(
            current_date=datetime.now().date().isoformat()
        )

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"messages": response}

    builder = StateGraph(AgentState)
    builder.add_node("record_training", record_training)
    tool_node = ToolNode(tools=[save_training_session])
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "record_training")
    builder.add_conditional_edges("record_training", tools_condition)
    builder.add_edge("tools", "record_training")

    return builder.compile()
