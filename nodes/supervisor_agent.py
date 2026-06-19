from typing import Literal

from llm_factory.llm_factory import LLMConfig, create_chat_model
from agent.state import AgentState
from nodes.meal_agent import make_meal_subgraph
from nodes.training_agent import make_training_subgraph

from pydantic import BaseModel

from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, START, END


SYSTEM_PROMPT="""You are a supervisor managing the following agents: 
- training_session_agent: call this subagent if user tells you about their training/workout sessions and it has not been handled yet.
- meal_record_agent: call this subagent if user tells you about their meals and it has not been handled yet.

Do not invoke these subagents outside of the above conditions.

CRITICAL: If the last message in the conversation is from an AI assistant confirming that the meal or workout was successfully saved, the task is complete. You MUST route to FINISH.

If the conversation is over, just general chatter, or the task is complete, route to FINISH.
"""

class RoutingDecision(BaseModel):
    """Decides the subagent to route work to, if no subagent needed, route to FINISH"""
    next_agent: Literal["training_session_agent", "meal_record_agent", "FINISH"]

def make_supervisor_agent(llm_config: LLMConfig, db_path: str, checkpointer=None):
    def supervisor_node(state):
        llm = create_chat_model(llm_config)

        system_prompt = SystemMessage(content=SYSTEM_PROMPT)
        system_msg = SystemMessage(content="Given the conversation above, who should act next?")
        supervisor_chain = llm.with_structured_output(RoutingDecision)
        decision = supervisor_chain.invoke([system_prompt] + state["messages"] + [system_msg])

        return {"next_agent": decision.next_agent}

    training_session_node = make_training_subgraph(llm_config, db_path)
    meal_record_node = make_meal_subgraph(llm_config, db_path)

    builder = StateGraph(AgentState)

    builder.add_node("supervisor_agent", supervisor_node)
    builder.add_node("training_session_agent", training_session_node)
    builder.add_node("meal_record_agent", meal_record_node)

    builder.add_edge(START, "supervisor_agent")
    builder.add_conditional_edges(
        "supervisor_agent",
        lambda state: state["next_agent"], 
        {
            "training_session_agent": "training_session_agent",
            "meal_record_agent": "meal_record_agent",
            "FINISH": END
        }
    )
    builder.add_edge("training_session_agent", "supervisor_agent")
    builder.add_edge("meal_record_agent", "supervisor_agent")

    return builder.compile(checkpointer=checkpointer)
