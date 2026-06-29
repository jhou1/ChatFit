from typing import Dict, Any

from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END

from models import AgentState
from utils.llm_factory import create_chat_model, LLMConfig
from prompts import ASSISTANT_SELECTION_INSTRUCTION
from agents.meal_recorder import make_record_meal_graph
from agents.training_recorder import make_record_training_graph


def route_assistant_on_relevance(llm_config: LLMConfig, messages: list) -> str:
    """
    Select the appropriate assistant based on conversation history
    """

    prompt_template = PromptTemplate.from_template(ASSISTANT_SELECTION_INSTRUCTION)
    system_prompt = prompt_template.format()
    
    recent_messages = messages
    routing_messages = [SystemMessage(content=system_prompt)] + recent_messages

    llm = create_chat_model(llm_config)
    chain = llm | StrOutputParser()
    response = chain.invoke(routing_messages)

    decision = [agent.strip() for agent in response.split(",")]
    return decision

def make_agent_graph(llm_config: LLMConfig, db_path: str, vector_store, checkpointer=None) -> StateGraph:
    training_recorder_node = make_record_training_graph(llm_config, db_path)
    meal_recorder_node = make_record_meal_graph(llm_config, db_path, vector_store)

    def training_wrapper(state: AgentState):
        result = training_recorder_node.invoke(state)
        return {"messages": result["messages"]}

    def meal_wrapper(state: AgentState):
        result = meal_recorder_node.invoke(state)
        return {"messages": result["messages"]}

    def chatter_node(state: AgentState):
        llm = create_chat_model(llm_config)
        messages = [SystemMessage(content="You are ChatFit, a friendly fitness and nutrition assistant. Answer general questions, say hello, and be helpful.")] + state["messages"]
        response = llm.invoke(messages)
        return {"messages": [response]}

    # routing node
    def assistant_selector_node(state: AgentState):
        decision = route_assistant_on_relevance(llm_config, state["messages"])
        return {"assistant_names": decision}

    # routing callable
    def route_decision(state: AgentState):
        if len(state["assistant_names"]) == 0:
            return ["chatter"]
        return state["assistant_names"]

    builder = StateGraph(AgentState)
    builder.add_node("training", training_wrapper)
    builder.add_node("meal", meal_wrapper)
    builder.add_node("chatter", chatter_node)
    builder.add_node("assistant_selector", assistant_selector_node)

    builder.add_edge(START, "assistant_selector")
    builder.add_conditional_edges(
        "assistant_selector",
        route_decision,
        {
            "training_agent": "training",
            "meal_agent": "meal",
            "chatter": "chatter"
        }
    )

    return builder.compile(checkpointer=checkpointer)
