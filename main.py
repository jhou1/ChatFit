import os
import uuid

from llm_factory.llm_factory import LLMConfig
from storage.db import init_db 
from nodes.supervisor_agent import make_supervisor_agent

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver


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

    print("========================================")
    print("ChatFit Agent Initialized. Type 'quit' to exit.")
    print("========================================\n")

    while True:
        user_input = input("\nUser: ")
        if user_input.lower() == 'quit':
            break

        if not user_input.strip():
            continue

        initial_state = {"messages": [HumanMessage(content=user_input)]}

        print("\nAgent: ", end="", flush=True)

        for event in app.stream(initial_state, config=config, stream_mode="updates"):
            for node_name, node_output in event.items():
               if node_name in ["training_session_agent", "meal_record_agent"]:
                   new_messages = node_output.get("messages", [])
                   if new_messages:
                       print(new_messages[-1].content)

               elif node_name == "supervisor_agent":
                   decision = node_output.get("next_agent", "FINISH")
                   print(f"\n[Supervisor routed to: {decision}]", end="", flush=True)


if __name__ == "__main__":
    main()
