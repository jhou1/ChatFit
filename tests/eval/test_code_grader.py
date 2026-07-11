import os
import yaml
import pytest
import logging
import sqlite3
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from agents.roles.supervisor import make_agent_graph
from agents.llm_factory import LLMConfig
from agents.rag import get_or_create_vector_store
from agents.sqlite_handler import init_db

# Suppress asyncio's "Task was destroyed but it is pending" stderr prints
# caused by underlying unawaited google-genai client teardown.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

def load_eval_cases():
    yaml_path = os.path.join(os.path.dirname(__file__), "eval_cases.yaml")
    with open(yaml_path, "r") as f:
        return yaml.safe_load(f)

@pytest.fixture
def mock_agent_env(tmp_path):
    llm_config = LLMConfig(provider="google", model_name="gemini-3.5-flash", temperature=0.0)
    db_path = str(tmp_path / "test_eval.db")
    init_db(db_path)
    vector_store = get_or_create_vector_store("./chroma_test_db")
    # Using MemorySaver for checkpointer to allow interrupts
    checkpointer = MemorySaver()
    app = make_agent_graph(llm_config, db_path, vector_store, checkpointer=checkpointer)
    return app, db_path

@pytest.mark.asyncio
@pytest.mark.parametrize("case", load_eval_cases(), ids=lambda c: c["id"])
async def test_agent_trajectory(case, mock_agent_env):
    mock_agent_graph, db_path = mock_agent_env
    user_input = case["input"]
    expected_tools = case.get("expected_tools", [])
    
    config = {"configurable": {"thread_id": case["id"]}}
    
    tool_calls_made = []
    routed_assistants = []
    
    async for event in mock_agent_graph.astream({"messages": [HumanMessage(content=user_input)]}, config=config, stream_mode="updates"):
        for node_name, node_output in event.items():
            if node_name == "assistant_selector":
                if isinstance(node_output, dict) and "assistant_names" in node_output:
                    routed_assistants.extend(node_output["assistant_names"])
            if node_name == "__interrupt__":
                for interrupt in node_output:
                    if hasattr(interrupt, "value") and isinstance(interrupt.value, dict):
                        for tc in interrupt.value.get("tool_calls", []):
                            tool_calls_made.append(tc)
                continue
                
            if isinstance(node_output, dict):
                messages = node_output.get("messages", [])
            else:
                continue
            for msg in messages:
                    if hasattr(msg, "tool_calls"):
                        for tc in msg.tool_calls:
                            tool_calls_made.append(tc)
                            
    # If the graph was interrupted (awaiting approval), approve it so it finishes DB writes
    state = await mock_agent_graph.aget_state(config)
    iterations = 0
    max_iterations = 5
    while state.next and iterations < max_iterations:
        # The agent is paused, simulate approval
        resume_data = {}
        for task in state.tasks:
            for intr in task.interrupts:
                resume_data[intr.id] = {"approved": True}
                
        if not resume_data:
            break
            
        async for event in mock_agent_graph.astream(Command(resume=resume_data), config=config, stream_mode="updates"):
            for node_name, node_output in event.items():
                if node_name == "assistant_selector":
                    if isinstance(node_output, dict) and "assistant_names" in node_output:
                        routed_assistants.extend(node_output["assistant_names"])
                if node_name == "__interrupt__":
                    for interrupt in node_output:
                        if hasattr(interrupt, "value") and isinstance(interrupt.value, dict):
                            for tc in interrupt.value.get("tool_calls", []):
                                tool_calls_made.append(tc)
                    continue
                if isinstance(node_output, dict):
                    messages = node_output.get("messages", [])
                else:
                    continue
                for msg in messages:
                    if hasattr(msg, "tool_calls"):
                        for tc in msg.tool_calls:
                            tool_calls_made.append(tc)
        state = await mock_agent_graph.aget_state(config)
                        
    # Evaluate expected tools
    actual_tool_names = [tc["name"] for tc in tool_calls_made]
    
    if len(expected_tools) == 0:
        assert len(tool_calls_made) == 0, f"Expected no tools, got {actual_tool_names}"
        return
        
    for expected in expected_tools:
        assert expected["name"] in actual_tool_names, f"Expected tool {expected['name']} not called."
        # Find the specific tool call
        for tc in tool_calls_made:
            if tc["name"] == expected["name"]:
                for arg_val in expected.get("args_contain", []):
                    assert arg_val.lower() in str(tc["args"]).lower(), f"Expected '{arg_val}' in arguments."
                    
    expected_routes = case.get("expected_routes", None)
    if expected_routes is not None:
        assert routed_assistants == expected_routes, f"Expected routes {expected_routes}, got {routed_assistants}"
        
    expected_db_state = case.get("expected_db_state", [])
    if expected_db_state:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            for state_check in expected_db_state:
                cursor.execute(state_check["query"])
                row = cursor.fetchone()
                assert row is not None, f"DB query {state_check['query']} returned no results"
                result = row[0]
                expected_val = state_check["expected_value"]
                assert result == expected_val, f"DB query {state_check['query']} returned {result}, expected {expected_val}"
