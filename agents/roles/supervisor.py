from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END

from agents.models import AgentState
from agents.llm_factory import create_chat_model, LLMConfig
from agents.roles.meal import make_meal_subagent_graph
from agents.roles.training import make_training_agent_graph

INSTRUCTION_FOR_ROUTING_SUBAGENTS="""
You skilled at assigning user input to the correct subagents.

These are the subagents you can assign to:
- training_agent: responsible for saving user training sessions to the database, invoke it when user tells you about their training/workout sessions.
- meal_agent: responsible for saving user meal details to the database, invoke it when user tells you about their meals.
- chatter: everything else.

Identify all relevant agents needed to process the user's message based on the conversation history. If the user is answering a clarification question from an agent (e.g., providing a missing detail about a training session or a meal), you MUST assign it back to the agent that asked the question.

Only output a comma-separated list of agents(e.g. training_agent, meal_agent, chatter)

Examples:
User input: I ran 15 km this morning and swam 1km this evening.
Response:
training_agent

User input: I had 2 eggs, 1 cup of milk this morning.
Response:
meal_agent

User input: I run 5km, eat an apple.
Response:
training_agent, meal_agent

User input: the weather is fine today
Response:
chatter
"""


def route_assistant_on_relevance(llm_config: LLMConfig, messages: list) -> list[str]:
    """
    Select the appropriate assistant based on conversation history
    """

    prompt_template = PromptTemplate.from_template(INSTRUCTION_FOR_ROUTING_SUBAGENTS)
    system_prompt = prompt_template.format()

    recent_messages = messages[-10:]
    history_text = "\n".join([f"{type(m).__name__}: {m.content}" for m in recent_messages])
    routing_input = f"Conversation History:\n{history_text}\n\nBased on the history above, return the assignment decision. Output ONLY a comma-separated list of agents (e.g. training_agent, meal_agent). If no agent is needed, output 'chatter'."
    routing_messages = [SystemMessage(content=system_prompt), HumanMessage(content=routing_input)]

    llm = create_chat_model(llm_config)
    chain = llm | StrOutputParser()
    response = chain.invoke(routing_messages)

    decision = [agent.strip() for agent in response.split(",") if "agent" in agent]
    if not decision:
        return ["chatter"]
    return decision

def make_agent_graph(llm_config: LLMConfig, db_path: str, vector_store, checkpointer=None) -> StateGraph:
    training_recorder_node = make_training_agent_graph(llm_config, db_path)
    meal_recorder_node = make_meal_subagent_graph(llm_config, db_path, vector_store)

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
