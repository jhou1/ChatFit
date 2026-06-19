from typing import Literal

from llm_factory.llm_factory import LLMConfig, create_chat_model
from agent.state import AgentState
from nodes.meal_agent import make_meal_subgraph
from nodes.training_agent import make_training_subgraph

from pydantic import BaseModel, Field

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import StateGraph, START, END


SYSTEM_PROMPT="""You are a helpful assistant to track the training/eating habits of the user. You manage the following agents: 
- training_session_agent which is responsible for saving user training sessions to the database, invoke it when user tells you about their training/workout sessions.
- meal_record_agent which is responsible for saving user meal details to the database, invoke it when user tells you about their meals.

Identify all relevant agents needed to process the user's message. If the user mentions both workouts and meals, return both agents."

If the conversation is over, just general chatter, or the task is complete, return an empty list.

ALWAYS speak short with brevity, use simple words, avoid adverb as much as possible.
"""

class RoutingDecision(BaseModel):
    """Decides the subagent to route work to"""
    next_agents: list[Literal["training_session_agent", "meal_record_agent"]]
    response: str = Field(description="A conversational response to the user") # not necessary, for chatting purpose

def route_agents(state):
    agents = state.get("next_agents", [])
    if not agents:
        return [END]
    return agents

def make_supervisor_agent(llm_config: LLMConfig, db_path: str, checkpointer=None):
    llm = create_chat_model(llm_config)
    supervisor_chain = llm.with_structured_output(RoutingDecision)

    def supervisor_node(state):
        system_prompt = SystemMessage(content=SYSTEM_PROMPT)
        system_msg = SystemMessage(content="Given the conversation above, who should act next?")
        decision = supervisor_chain.invoke([system_prompt] + state["messages"] + [system_msg])
        return {
            "next_agents": decision.next_agents, 
            "messages": [AIMessage(content=decision.response)]
        }

    training_session_node = make_training_subgraph(llm_config, db_path)
    meal_record_node = make_meal_subgraph(llm_config, db_path)

    builder = StateGraph(AgentState)

    builder.add_node("supervisor_agent", supervisor_node)
    builder.add_node("training_session_agent", training_session_node)
    builder.add_node("meal_record_agent", meal_record_node)

    builder.add_edge(START, "supervisor_agent")
    builder.add_conditional_edges(
        "supervisor_agent",
        route_agents,
        {
            "training_session_agent": "training_session_agent",
            "meal_record_agent": "meal_record_agent",
            END: END
        }
    )
    builder.add_edge("training_session_agent", END)
    builder.add_edge("meal_record_agent", END)

    return builder.compile(checkpointer=checkpointer)
