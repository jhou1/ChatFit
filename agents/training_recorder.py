from datetime import datetime

from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage

from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition

from models import RecordTrainingInput, AgentState
from utils.db import add_training_session, get_training_sessions_of_last_n_days
from utils.llm_factory import create_chat_model, LLMConfig
from prompts import TRAINING_SESSION_ADDITION_INSTRUCTION, TRAINING_SESSION_RETRIEVAL_INSTRUCTION

def make_record_training_graph(llm_config: LLMConfig, db_path: str):

    @tool(args_schema=RecordTrainingInput)
    def save_training_session(**kwargs):
        """Add the user training log to db."""

        input_data = RecordTrainingInput(**kwargs)
        return add_training_session(input_data, db_path)

    @tool
    def retrieve_training_sessions(num_of_days: int):
        """Get a list of training sessions of the last n days"""
        return get_training_sessions_of_last_n_days(num_of_days, db_path)


    llm = create_chat_model(llm_config)
    llm_with_tools = llm.bind_tools([save_training_session,
                                     retrieve_training_sessions
                                     ])

    def record_training(state: AgentState):
        prompt_template = PromptTemplate.from_template(TRAINING_SESSION_ADDITION_INSTRUCTION)
        system_prompt = prompt_template.format(
            current_date=datetime.now().date().isoformat()
        )

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"messages": response}

    def retrieve_training(state: AgentState):
        system_message = SystemMessage(content=TRAINING_SESSION_RETRIEVAL_INSTRUCTION)
        messages = [SystemMessage(content=system_message)] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"message": response}

    builder = StateGraph(AgentState)
    builder.add_node("record_training", record_training)
    builder.add_node("retrieve_training", retrieve_training)
    tool_node = ToolNode(tools=[save_training_session,
                                retrieve_training_sessions])
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "record_training")
    builder.add_conditional_edges("record_training", tools_condition)
    builder.add_conditional_edges("retrieve_training", tools_condition)
    builder.add_edge("tools", "record_training")

    return builder.compile()
