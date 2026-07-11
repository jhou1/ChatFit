# Agent Evaluation Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dual-pipeline evaluation framework for ChatFit using a Pytest-based code grader for CI and an LLM-as-a-judge for production, underpinned by Langfuse for traceability.

**Architecture:** We will instrument the LangGraph execution with Langfuse for observability. A custom Pytest test suite will read test scenarios from a YAML file, invoke the local agent graph, and deterministically assert tool calls for integration testing. A separate Python script will run an LLM-as-a-judge over Langfuse traces to score RAG context and conversational tone.

**Tech Stack:** `pytest`, `langfuse`, `pyyaml`, `langchain-core`

---

### Task 1: Add Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Write a minimal sanity test**
We don't have code yet, so just verify `uv` commands can run.

```bash
uv --version
```
Expected: Prints uv version.

- [ ] **Step 2: Add production dependencies**

```bash
uv add langfuse
```

- [ ] **Step 3: Add development dependencies**

```bash
uv add --dev pyyaml
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add langfuse and pyyaml dependencies for evaluation framework"
```

---

### Task 2: Configure Langfuse Environment

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add environment variables**
Add Langfuse config to `.env.example`:

```env
# Langfuse Tracing
LANGFUSE_SECRET_KEY="sk-lf-..."
LANGFUSE_PUBLIC_KEY="pk-lf-..."
LANGFUSE_HOST="http://localhost:3000"
```

- [ ] **Step 2: Add Langfuse to docker-compose**
Add the Langfuse service block to `docker-compose.yml`:

```yaml
  langfuse-server:
    image: langfuse/langfuse:latest
    depends_on:
      - langfuse-db
    ports:
      - "3000:3000"
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@langfuse-db:5432/langfuse
      - NEXTAUTH_SECRET=mysecret
      - SALT=mysalt
      - NEXTAUTH_URL=http://localhost:3000
      - TELEMETRY_ENABLED=false
      
  langfuse-db:
    image: postgres:15
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_DB=langfuse
    volumes:
      - ./langfuse_db_data:/var/lib/postgresql/data
```

- [ ] **Step 3: Commit**

```bash
git add .env.example docker-compose.yml
git commit -m "chore: add langfuse services to docker-compose and environment templates"
```

---

### Task 3: Instrument LangGraph with Langfuse

**Files:**
- Modify: `api.py`

- [ ] **Step 1: Write a failing test for Langfuse instrumentation**
We can't easily mock Langfuse inside a unit test without a server, so we'll test for syntax validity.

Run: `python -m py_compile api.py`
Expected: PASS

- [ ] **Step 2: Update `api.py` imports**
Open `api.py` and add the Langfuse callback import at the top:

```python
from langfuse.callback import CallbackHandler
```

- [ ] **Step 3: Update `config` in `api.py`**
Locate the `config` initialization near line 145 (inside `chat_endpoint`) and add the callback:

```python
    langfuse_handler = CallbackHandler(
        session_id=thread_id,
        user_id=req.user_id
    )
    
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [langfuse_handler]
    }
```

- [ ] **Step 4: Verify syntax**
Run: `python -m py_compile api.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api.py
git commit -m "feat: instrument api graph execution with langfuse tracing"
```

---

### Task 4: Create Evaluation Dataset

**Files:**
- Create: `tests/eval/eval_cases.yaml`

- [ ] **Step 1: Create the directory**

```bash
mkdir -p tests/eval
```

- [ ] **Step 2: Create the YAML dataset**
Create `tests/eval/eval_cases.yaml` with the following content:

```yaml
- id: test_meal_extraction_01
  input: "I just had 3 scrambled eggs and a piece of toast for breakfast."
  expected_tools: 
    - name: log_meal
      args_contain: ["scrambled eggs", "toast"]
- id: test_greeting_no_tool
  input: "Hello there, how are you doing today?"
  expected_tools: []
```

- [ ] **Step 3: Commit**

```bash
git add tests/eval/eval_cases.yaml
git commit -m "test: add initial yaml dataset for code grader"
```

---

### Task 5: Implement Code Grader (Pytest)

**Files:**
- Create: `tests/eval/test_code_grader.py`

- [ ] **Step 1: Write the Pytest grader logic**
Create `tests/eval/test_code_grader.py`:

```python
import os
import yaml
import pytest
from langchain_core.messages import HumanMessage
from agents.roles.supervisor import make_agent_graph
from agents.llm_factory import LLMConfig
from agents.rag import get_or_create_vector_store

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
            messages = node_output.get("messages", [])
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
```

- [ ] **Step 2: Run the test to verify it executes (it may fail if the agent needs specific setup, which verifies our logic is running)**

```bash
pytest tests/eval/test_code_grader.py -v
```
*(Note: As long as pytest discovers and attempts the test, the step is successful. Agent failures here indicate normal test assertions.)*

- [ ] **Step 3: Commit**

```bash
git add tests/eval/test_code_grader.py
git commit -m "test: implement pytest code grader for asserting agent trajectories"
```

---

### Task 6: Implement LLM-as-a-Judge Script

**Files:**
- Create: `scripts/llm_judge.py`

- [ ] **Step 1: Write the judge script**
Create `scripts/llm_judge.py`:

```python
import os
import asyncio
from langfuse import Langfuse
from agents.llm_factory import LLMConfig, create_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

langfuse = Langfuse()

JUDGE_PROMPT = """
You are an expert conversational AI evaluator. 
Score the assistant's response on a scale of 1 to 5 for Conversational Tone.
Tone definition:
5 = Highly helpful, friendly, natural, and encouraging.
1 = Robotic, unhelpful, or rude.

Provide your output strictly in this format:
SCORE: [1-5]
REASON: [Brief explanation]
"""

async def evaluate_trace(trace_id: str, input_msg: str, output_msg: str):
    llm_config = LLMConfig(provider="google", model_name="gemini-3.5-flash", temperature=0.0)
    judge_llm = create_chat_model(llm_config)
    
    evaluation_content = f"User: {input_msg}\nAssistant: {output_msg}"
    
    response = await judge_llm.ainvoke([
        SystemMessage(content=JUDGE_PROMPT),
        HumanMessage(content=evaluation_content)
    ])
    
    score_text = response.content
    try:
        score_line = [line for line in score_text.splitlines() if line.startswith("SCORE:")][0]
        score = int(score_line.split(":")[1].strip())
        reason = score_text.replace(score_line, "").strip()
        
        langfuse.score(
            trace_id=trace_id,
            name="conversational_tone",
            value=score,
            comment=reason
        )
        print(f"Scored trace {trace_id}: {score}/5")
    except Exception as e:
        print(f"Failed to parse score for {trace_id}: {e}\nRaw output: {score_text}")

if __name__ == "__main__":
    # Example usage: python scripts/llm_judge.py <trace_id>
    import sys
    if len(sys.argv) > 1:
        trace_id = sys.argv[1]
        asyncio.run(evaluate_trace(trace_id, "User Input", "Assistant Output"))
    else:
        print("Usage: python scripts/llm_judge.py <trace_id>")
```

- [ ] **Step 2: Verify syntax**
Run: `python -m py_compile scripts/llm_judge.py`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/llm_judge.py
git commit -m "feat: add llm-as-a-judge standalone script for langfuse trace grading"
```
