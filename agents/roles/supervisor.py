from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import StateGraph, START
from langgraph.graph.state import CompiledStateGraph

from agents.models import AgentState
from agents.llm_factory import create_chat_model, LLMConfig
from agents.memory.agent import LLMMemoryInterpreter, MemoryAgent, MemoryInterpreter
from agents.memory.commands import parse_memory_command, should_auto_route_memory
from agents.memory.context import append_agent_context, format_durable_memories
from agents.memory.models import PendingMemoryAction
from agents.memory.store import UserMemoryStore, owner_key_for
from agents.roles.meal import make_meal_subagent_graph
from agents.roles.training import make_training_agent_graph
from agents.roles.insights import make_insights_agent_graph

from tools.safe_execution import _execute_llm_query_safely
from agents.utils import extract_text
from agents.observability import content_attributes, emit_event, observe_span

INSTRUCTION_FOR_ROUTING_SUBAGENTS = """
You skilled at assigning user input to the correct subagents.

These are the subagents you can assign to:
- memory_agent: responsible only for explicit requests to remember, update, or forget durable user memories.
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
You are a conversation context summarizer. Compress the following conversation history into a concise summary.
Focus on training(fitness) goals, dietary context, user preferences, and any important ongoing context.

Here is the existing summary that you must merge with the new information:
{existing_summary}

Here is the new conversation history to compress:
{summary_text}
"""

_ROUTABLE_AGENTS = frozenset(
    ("training_agent", "meal_agent", "insights_agent", "chatter")
)


async def route_assistant_on_relevance(
    llm_config: LLMConfig,
    messages: list,
    *,
    pending_memory_action=None,
    resolved_memory_target: bool = False,
    memory_context: str | None = None,
    summary: str | None = None,
) -> list[str]:
    """Select the appropriate assistant based on conversation history"""

    prompt_template = PromptTemplate.from_template(INSTRUCTION_FOR_ROUTING_SUBAGENTS)
    system_prompt = append_agent_context(
        prompt_template.format(),
        memory_context=memory_context,
        summary=summary,
    )

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

    selected = []
    if "LLM request timeout exceeded" not in content_str:
        selected = [agent.strip() for agent in content_str.split(",")]
    routed = [agent for agent in selected if agent in _ROUTABLE_AGENTS]

    latest_user_message = next(
        (
            extract_text(message)
            for message in reversed(messages)
            if isinstance(message, HumanMessage)
        ),
        "",
    )
    command = parse_memory_command(latest_user_message)
    router_authorized_memory = (
        "memory_agent" in selected and command is not None and resolved_memory_target
    )
    if (
        pending_memory_action is not None
        or should_auto_route_memory(command)
        or router_authorized_memory
    ):
        routed.insert(0, "memory_agent")

    routed = list(dict.fromkeys(routed))
    if any(agent != "chatter" for agent in routed):
        routed = [agent for agent in routed if agent != "chatter"]
    return routed or ["chatter"]


def make_agent_graph(
    llm_config: LLMConfig,
    db_path: str,
    vector_store,
    checkpointer=None,
    *,
    memory_store: UserMemoryStore | None = None,
    memory_interpreter: MemoryInterpreter | None = None,
) -> CompiledStateGraph:
    memory_store = memory_store or UserMemoryStore(f"{db_path}.user-memory.db")
    memory_interpreter = memory_interpreter or LLMMemoryInterpreter(llm_config)
    durable_memory_agent = MemoryAgent(
        store=memory_store,
        interpreter=memory_interpreter,
    )
    training_recorder_node = make_training_agent_graph(llm_config, db_path)
    meal_recorder_node = make_meal_subagent_graph(llm_config, db_path, vector_store)
    insights_recorder_node = make_insights_agent_graph(llm_config, db_path)

    def configured_user_id(config: RunnableConfig) -> str:
        configurable = config.get("configurable", {})
        user_id = configurable.get("user_id") or configurable.get("thread_id")
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("configurable.user_id or thread_id is required")
        return user_id

    def fresh_memory_context(config: RunnableConfig) -> str:
        user_id = configured_user_id(config)
        memories = memory_store.list_memories(owner_key_for(user_id))
        return format_durable_memories(memories)

    def resolves_current_memory_target(
        state: AgentState, config: RunnableConfig
    ) -> bool:
        user_message = next(
            (
                extract_text(message)
                for message in reversed(state["messages"])
                if isinstance(message, HumanMessage)
            ),
            "",
        )
        command = parse_memory_command(user_message)
        if command is None or command.operation not in ("update", "forget"):
            return False
        owner_key = owner_key_for(configured_user_id(config))
        try:
            matching_ids = {
                memory.id
                for query in command.target_queries
                for memory in memory_store.resolve(owner_key, query)
            }
        except Exception:
            return False
        return len(matching_ids) == 1

    async def invoke_specialist(
        specialist,
        state: AgentState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        memory_context = fresh_memory_context(config)
        refreshed_state = {**state, "memory_context": memory_context}
        result = await specialist.ainvoke(refreshed_state)
        return {"messages": result["messages"], "memory_context": memory_context}

    async def training_wrapper(state: AgentState, config: RunnableConfig):
        with observe_span("agent.training"):
            return await invoke_specialist(training_recorder_node, state, config)

    async def meal_wrapper(state: AgentState, config: RunnableConfig):
        with observe_span("agent.meal"):
            return await invoke_specialist(meal_recorder_node, state, config)

    async def insights_wrapper(state: AgentState, config: RunnableConfig):
        with observe_span("agent.insights"):
            return await invoke_specialist(insights_recorder_node, state, config)

    async def chatter_node(state: AgentState):
        with observe_span("agent.chatter"):
            llm = create_chat_model(llm_config)
            system_msg = "You are ChatFit, a friendly fitness and nutrition assistant. Answer general questions, say hello, and be helpful."
            system_msg = append_agent_context(
                system_msg,
                memory_context=state.get("memory_context"),
                summary=state.get("summary"),
            )
            messages = [SystemMessage(content=system_msg)] + state["messages"]
            response = await _execute_llm_query_safely(llm, messages)
            return {"messages": [response["messages"]]}

    async def load_memories_node(
        state: AgentState, config: RunnableConfig
    ) -> dict[str, str]:
        del state
        return {"memory_context": fresh_memory_context(config)}

    async def memory_agent_node(
        state: AgentState, config: RunnableConfig
    ) -> dict[str, object]:
        user_message = next(
            (
                extract_text(message)
                for message in reversed(state["messages"])
                if isinstance(message, HumanMessage)
            ),
            "",
        )
        user_id = configured_user_id(config)
        pending_raw = state.get("pending_memory_action")
        pending = (
            PendingMemoryAction.model_validate(pending_raw)
            if pending_raw is not None
            else None
        )
        result = await durable_memory_agent.handle(
            user_id=user_id,
            user_message=user_message,
            pending=pending,
        )
        refreshed = memory_store.list_memories(owner_key_for(user_id))
        return {
            "messages": [AIMessage(content=result.response)],
            "pending_memory_action": (
                result.pending.model_dump(mode="json")
                if result.pending is not None
                else None
            ),
            "memory_context": format_durable_memories(refreshed),
        }

    async def context_governance_node(state: AgentState):
        """cut off the messages when its length exceeds max length
        the messages to be cutoff are compressed using LLM
        if the messages contain ToolMessage or AIMessage with tool calls,
        then shift the cut off index dynamically to prevent cutting off tool messages
        """
        messages = state["messages"]
        with observe_span(
            "context_governance",
            {
                "context.messages.before": len(messages),
                "context.summary.present": bool(state.get("summary")),
            },
        ):
            MAX_MESSAGES_LENGTH = 20
            if len(messages) < MAX_MESSAGES_LENGTH:
                emit_event("context.governance_skipped", {"reason": "below_limit"})
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
            response = await _execute_llm_query_safely(
                llm, [HumanMessage(content=prompt)]
            )

            new_summary = extract_text(response["messages"])
            emit_event(
                "context.governance_completed",
                {
                    "context.messages.removed": len(messages_to_compress),
                    **content_attributes(new_summary, "context.summary"),
                },
            )

            delete_cmd = [
                RemoveMessage(id=message.id)
                for message in messages_to_compress
                if message.id
            ]
            return {"summary": new_summary, "messages": delete_cmd}

    # routing node
    async def assistant_selector_node(state: AgentState, config: RunnableConfig):
        with observe_span(
            "assistant_selector",
            {"context.messages": len(state["messages"])},
        ):
            decision = await route_assistant_on_relevance(
                llm_config,
                state["messages"],
                pending_memory_action=state.get("pending_memory_action"),
                resolved_memory_target=resolves_current_memory_target(state, config),
                memory_context=state.get("memory_context"),
                summary=state.get("summary"),
            )
            emit_event(
                "agent.route.decided",
                {
                    "route.selected_agents": decision,
                    "route.fallback_used": decision == ["chatter"],
                },
            )
            return {"assistant_names": decision}

    # routing callable
    def route_decision(state: AgentState):
        if len(state["assistant_names"]) == 0:
            return ["chatter"]
        return state["assistant_names"]

    builder = StateGraph(AgentState)
    builder.add_node("load_memories", load_memories_node)
    builder.add_node("context_governance", context_governance_node)
    builder.add_node("training", training_wrapper)
    builder.add_node("meal", meal_wrapper)
    builder.add_node("insights", insights_wrapper)
    builder.add_node("chatter", chatter_node)
    builder.add_node("assistant_selector", assistant_selector_node)
    builder.add_node("memory", memory_agent_node)

    builder.add_edge(START, "load_memories")
    builder.add_edge("load_memories", "context_governance")
    builder.add_edge("context_governance", "assistant_selector")
    builder.add_conditional_edges(
        "assistant_selector",
        route_decision,
        {
            "training_agent": "training",
            "meal_agent": "meal",
            "insights_agent": "insights",
            "memory_agent": "memory",
            "chatter": "chatter",
        },
    )

    return builder.compile(checkpointer=checkpointer)
