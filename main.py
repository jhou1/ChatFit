import os
import uuid

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

# Rich
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from utils.llm_factory import LLMConfig
from utils.db import init_db
from agents.assistant_selector import make_agent_graph
from rag import get_or_create_vector_store

console = Console()

def main():
    llm_config = LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        temperature=0.5,
        max_tokens=2048,
        kwargs={"client_args": {"proxy": "socks5://127.0.0.1:8990"}}
    )

    db_path = "chatfit.db"
    if not os.path.exists(db_path):
        init_db(db_path)

    with console.status("[bold yellow]Loading Cookbook ...[/]", spinner="dots"):
        vector_store = get_or_create_vector_store("~/Documents/LifeOS/下厨房/", "chroma.db")


    # init memory
    app = make_agent_graph(llm_config, db_path, vector_store, checkpointer=MemorySaver())
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    welcome_msg = "[bold green]ChatFit Agent Initialized.[/]\nType [bold red]'quit'[/] to exit."
    console.print(Panel(welcome_msg, title="ChatFit", expand=False, border_style="cyan"))


    while True:
        user_input = Prompt.ask("\b[bold blue]You[/]")
        if user_input.lower() == "quit" or user_input.lower() == "exit":
            console.print("[bold red]Goodbye![/]")
            break

        if not user_input.strip():
            continue

        initial_state = {"messages": [HumanMessage(content=user_input)]}

        with console.status("[bold yellow]Agent is thinking...[/]", spinner="dots"):
            for event in app.stream(initial_state, config=config, stream_mode="updates"):
                for node_name, node_output in event.items():
                    if node_name in ["training", "meal", "assistant_selector", "chatter"]:
                        new_messages = node_output.get("messages", [])
                        if new_messages:
                            last_message = new_messages[-1]

                            # Handle Gemini's list-based content (extract text parts)
                            if isinstance(last_message.content, list):
                                text_content = "".join(
                                    part.get("text", "") for part in last_message.content
                                    if isinstance(part, dict) and "text" in part
                                )
                            else:
                                text_content = str(last_message.content)

                            if text_content.strip():
                                console.print(f"[bold green]Agent ({node_name}):[/] {text_content}\n")

                    elif node_name == "supervisor_agent":
                        decision = node_output.get("next_agents", []) # Note: ensure this matches your parallel setup fix if applied!
                        console.print(f"[dim italic]Supervisor routed to: {decision}[/]")


if __name__ == "__main__":
    main()
