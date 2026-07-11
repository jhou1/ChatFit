import os
import yaml
import pytest
import logging
from langchain_core.messages import HumanMessage
from agents.roles.supervisor import make_agent_graph
from agents.llm_factory import LLMConfig
from agents.rag import get_or_create_vector_store

# Suppress asyncio's "Task was destroyed but it is pending" stderr prints
# caused by underlying unawaited google-genai client teardown.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

def load_eval_cases():
    yaml_path = os.path.join(os.path.dirname(__file__), "eval_cases.yaml")
    with open(yaml_path, "r") as f:
        return yaml.safe_load(f)

@pytest.fixture
def mock_agent_graph():
    llm_config = LLMConfig(provider="google", model_name="gemini-3.5-flash", temperature=0.0)
    db_path = ":memory:"
    vector_store = get_or_create_vector_store("./chroma_test_db")
    # Using no checkpointer for pure functional eval run
    return make_agent_graph(llm_config, db_path, vector_store, checkpointer=None)

@pytest.mark.asyncio
@pytest.mark.parametrize("case", load_eval_cases(), ids=lambda c: c["id"])
async def test_agent_trajectory(case, mock_agent_graph):
    user_input = case["input"]
    expected_tools = case.get("expected_tools", [])
    
    config = {"configurable": {"thread_id": case["id"]}}
    
    tool_calls_made = []
    
    async for event in mock_agent_graph.astream({"messages": [HumanMessage(content=user_input)]}, config=config, stream_mode="updates"):
        for node_name, node_output in event.items():
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
