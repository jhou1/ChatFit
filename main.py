import os
import uuid

from llm_factory.llm_factory import LLMConfig
from storage.db import init_db 
from nodes.supervisor_agent import make_supervisor_agent

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

# Rich
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()

def main():
    # llm configuration
    llm_config = LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        temperature=0.5,
        max_tokens=2048,
        kwargs={"client_args": {"proxy": "socks5://127.0.0.1:8990"}}
    )

    # db configuraiton
    db_path = "chatfit.db"
    if not os.path.exists(db_path):
        init_db(db_path)


    # init memory
    memory = MemorySaver()
    app = make_supervisor_agent(llm_config, db_path, checkpointer=memory)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    welcome_msg = "[bold green]ChatFit Agent Initialized.[/]\nType [bold red]'quit'[/] to exit."
    console.print(Panel(welcome_msg, title="ChatFit", expand=False, border_style="cyan"))


    while True:
        user_input = Prompt.ask("\b[bold blue]You[/]")
        if user_input.lower() == 'quit':
            console.print("[bold red]Goodbye![/]")
            break

        if not user_input.strip():
            continue

        initial_state = {"messages": [HumanMessage(content=user_input)]}

        with console.status("[bold yellow]Agent is thinking...[/]", spinner="dots"):
            for event in app.stream(initial_state, config=config, stream_mode="updates"):
                for node_name, node_output in event.items():
                    if node_name in ["training_session_agent", "meal_record_agent", "supervisor_agent"]:
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
