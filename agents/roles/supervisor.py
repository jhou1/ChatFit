from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import StateGraph, START

from agents.models import AgentState
from agents.llm_factory import create_chat_model, LLMConfig
from agents.roles.meal import make_meal_subagent_graph
from agents.roles.training import make_training_agent_graph
from agents.roles.insights import make_insights_agent_graph

from tools.safe_execution import _execute_llm_query_safely
from agents.utils import extract_text

INSTRUCTION_FOR_ROUTING_SUBAGENTS = """
You skilled at assigning user input to the correct subagents.

These are the subagents you can assign to:
- training_agent: responsible for saving user training sessions to the database, invoke it when user tells you about their training/workout sessions.
- meal_agent: responsible for saving user meal details to the database, invoke it when user tells you about their meals.
- insights_agent: responsible for analyzing training progress, intensity, recovery, waveness, or answering questions about "am I training too much", "how is my consistency".
- chatter: everything else.

Identify all relevant agents needed to process the user's message based on the conversation history. If the user is answering a clarification question from an agent (e.g., providing a missing detail about a training session or a meal), you MUST assign it back to the agent that asked the question.

Only output a comma-separated list of agents(e.g. training_agent, meal_agent, insights_agent, chatter)

Examples:
User input: I ran 15 km this morning and swam 1km this evening.
Response:
training_agent

User input: Am I training too hard lately? Can you analyze my recovery?
Response:
insights_agent

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

CONTEXT_GOVERNANCE_PROMPT = """
You are an assistant memory manager. Compress the following conversation history into a concise summary.
Focus on training(fitness) goals, dietary context, user preferences, and any important ongoing context.

Here is the existing summary that you must merge with the new information:
{existing_summary}

Here is the new conversation history to compress:
{summary_text}
"""


async def route_assistant_on_relevance(
    llm_config: LLMConfig, messages: list
) -> list[str]:
    """Select the appropriate assistant based on conversation history"""

    prompt_template = PromptTemplate.from_template(INSTRUCTION_FOR_ROUTING_SUBAGENTS)
    system_prompt = prompt_template.format()

    recent_messages = messages[-10:]
    history_text = "\n".join(
        [f"{type(m).__name__}: {m.content}" for m in recent_messages]
    )
    routing_input = f"Conversation History:\n{history_text}\n\nBased on the history above, return the assignment decision. Output ONLY a comma-separated list of agents (e.g. training_agent, meal_agent). If no agent is needed, output 'chatter'."
    routing_messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=routing_input),
    ]

    llm = create_chat_model(llm_config)
    # chain = llm | StrOutputParser()
    response = await _execute_llm_query_safely(llm, routing_messages)
    content_str = extract_text(response["messages"])

    if "LLM request timeout exceeded" in content_str:
        return ["chatter"]

    decision = [agent.strip() for agent in content_str.split(",") if "agent" in agent]
    if not decision:
        return ["chatter"]
    return decision


def make_agent_graph(
    llm_config: LLMConfig, db_path: str, vector_store, checkpointer=None
) -> StateGraph:
    training_recorder_node = make_training_agent_graph(llm_config, db_path)
    meal_recorder_node = make_meal_subagent_graph(llm_config, db_path, vector_store)
    insights_recorder_node = make_insights_agent_graph(llm_config, db_path)

    async def training_wrapper(state: AgentState):
        result = await training_recorder_node.ainvoke(state)
        return {"messages": result["messages"]}

    async def meal_wrapper(state: AgentState):
        result = await meal_recorder_node.ainvoke(state)
        return {"messages": result["messages"]}

    async def insights_wrapper(state: AgentState):
        result = await insights_recorder_node.ainvoke(state)
        return {"messages": result["messages"]}

    async def chatter_node(state: AgentState):
        llm = create_chat_model(llm_config)
        system_msg = "You are ChatFit, a friendly fitness and nutrition assistant. Answer general questions, say hello, and be helpful."
        # adding previous conversation summary as context
        if state.get("summary"):
            system_msg += f"\n\n[Historical Conversation Summary]:\n{state['summary']}"
        messages = [SystemMessage(content=system_msg)] + state["messages"]
        response = await _execute_llm_query_safely(llm, messages)
        return {"messages": [response["messages"]]}

    async def context_governance_node(state: AgentState):
        """cut off the messages when its length exceeds max length
        the messages to be cutoff are compressed using LLM
        if the messages contain ToolMessage or AIMessage with tool calls,
        then shift the cut off index dynamically to prevent cutting off tool messages
        """
        messages = state["messages"]
        MAX_MESSAGES_LENGTH = 20
        if len(messages) < MAX_MESSAGES_LENGTH:
            return

        message_cutoff_index = 10
        while message_cutoff_index < len(messages):
            msg = messages[message_cutoff_index]
            if isinstance(msg, ToolMessage):
                message_cutoff_index += 1
            elif isinstance(msg, AIMessage) and getattr(msg, "tool_calls", []):
                message_cutoff_index += 1
            else:
                break
        messages_to_compress = messages[:message_cutoff_index]

        summary_text = ""
        for message in messages_to_compress:
            text = extract_text(message)
            if text.strip():
                summary_text += f"{type(message).__name__}: {text}\n"

        existing_summary = state["summary"] if state.get("summary") else ""
        prompt_template = PromptTemplate.from_template(CONTEXT_GOVERNANCE_PROMPT)
        prompt = prompt_template.format(
            existing_summary=existing_summary, summary_text=summary_text
        )

        llm = create_chat_model(llm_config)
        response = await _execute_llm_query_safely(llm, [HumanMessage(content=prompt)])

        new_summary = extract_text(response["messages"])

        delete_cmd = [
            RemoveMessage(id=message.id)
            for message in messages_to_compress
            if message.id
        ]
        return {"summary": new_summary, "messages": delete_cmd}

    # routing node
    async def assistant_selector_node(state: AgentState):
        decision = await route_assistant_on_relevance(llm_config, state["messages"])
        return {"assistant_names": decision}

    # routing callable
    def route_decision(state: AgentState):
        if len(state["assistant_names"]) == 0:
            return ["chatter"]
        return state["assistant_names"]

    builder = StateGraph(AgentState)
    builder.add_node("context_governance", context_governance_node)
    builder.add_node("training", training_wrapper)
    builder.add_node("meal", meal_wrapper)
    builder.add_node("insights", insights_wrapper)
    builder.add_node("chatter", chatter_node)
    builder.add_node("assistant_selector", assistant_selector_node)

    builder.add_edge(START, "context_governance")
    builder.add_edge("context_governance", "assistant_selector")
    builder.add_conditional_edges(
        "assistant_selector",
        route_decision,
        {
            "training_agent": "training",
            "meal_agent": "meal",
            "insights_agent": "insights",
            "chatter": "chatter",
        },
    )

    return builder.compile(checkpointer=checkpointer)
