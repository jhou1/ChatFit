# Advanced Evaluation Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the evaluation framework to grade supervisor routing accuracy and end-to-end task completion (verifying SQLite database writes).

**Architecture:** We will modify the Pytest test runner to track the `assistant_selector` node output for routing verification. We will also refactor the Pytest fixture to use a temporary on-disk SQLite database (instead of `:memory:`) so that we can assert raw SQL queries against it after the agent finishes execution.

**Tech Stack:** `pytest`, `sqlite3`, `pyyaml`

---

### Task 1: Refactor Test Fixture for Database Persistence

**Files:**
- Modify: `tests/eval/test_code_grader.py`

- [ ] **Step 1: Write the failing test**
We don't need a new test case yet. We just refactor the fixture so that the db state persists for our assertions in Task 3. In `tests/eval/test_code_grader.py`, replace `mock_agent_graph` with `mock_agent_env` that returns both the app and the db path.

```python
from agents.sqlite_handler import init_db

@pytest.fixture
def mock_agent_env(tmp_path):
    llm_config = LLMConfig(provider="google", model_name="gemini-3.5-flash", temperature=0.0)
    db_path = str(tmp_path / "test_eval.db")
    init_db(db_path)
    vector_store = get_or_create_vector_store("./chroma_test_db")
    # Using no checkpointer for pure functional eval run
    app = make_agent_graph(llm_config, db_path, vector_store, checkpointer=None)
    return app, db_path
```

- [ ] **Step 2: Update the test signature and graph invocation**
In the same file, update `test_agent_trajectory` to use the new fixture:

```python
async def test_agent_trajectory(case, mock_agent_env):
    mock_agent_graph, db_path = mock_agent_env
    user_input = case["input"]
```

- [ ] **Step 3: Run tests to verify they still pass with the temp db**

```bash
uv run pytest tests/eval/test_code_grader.py -v
```
Expected: PASS (7/7 tests passed)

- [ ] **Step 4: Commit**

```bash
git add tests/eval/test_code_grader.py
git commit -m "test: refactor code grader fixture to use temporary db for state evaluation"
```

---

### Task 2: Evaluate Supervisor Routing

**Files:**
- Modify: `tests/eval/eval_cases.yaml`
- Modify: `tests/eval/test_code_grader.py`

- [ ] **Step 1: Add a routing test case**
Open `tests/eval/eval_cases.yaml` and add `expected_routes` to the `test_routing_chatter` case:

```yaml
- id: test_routing_chatter
  input: "the weather is fine today"
  expected_tools: []
  expected_routes: ["chatter"]
```

- [ ] **Step 2: Run test to verify it fails (or doesn't assert yet)**
The test won't fail because we haven't written the assertion, but it's good to ensure YAML parsing still works.

- [ ] **Step 3: Write routing tracking and assertion implementation**
In `tests/eval/test_code_grader.py`, update `test_agent_trajectory` to track routed assistants and assert them.

Right before `async for event in mock_agent_graph.astream(...)`, add:
```python
    routed_assistants = []
```

Inside the `async for` loop, right after `for node_name, node_output in event.items():`, add:
```python
            if node_name == "assistant_selector":
                if isinstance(node_output, dict) and "assistant_names" in node_output:
                    routed_assistants.extend(node_output["assistant_names"])
```

At the very end of the `test_agent_trajectory` function, add the assertion:
```python
    expected_routes = case.get("expected_routes", None)
    if expected_routes is not None:
        assert routed_assistants == expected_routes, f"Expected routes {expected_routes}, got {routed_assistants}"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/eval/test_code_grader.py::test_agent_trajectory\[test_routing_chatter\] -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/eval/eval_cases.yaml tests/eval/test_code_grader.py
git commit -m "test: evaluate supervisor agent routing accuracy in code grader"
```

---

### Task 3: Evaluate Task Completion (Database State)

**Files:**
- Modify: `tests/eval/eval_cases.yaml`
- Modify: `tests/eval/test_code_grader.py`

- [ ] **Step 1: Add a DB state test case**
Open `tests/eval/eval_cases.yaml` and add an `expected_db_state` block to the `test_meal_breakfast` case:

```yaml
- id: test_meal_breakfast
  input: "breakfast: 2 fried eggs and bread today"
  expected_tools:
    - name: log_meal
      args_contain: ["fried eggs", "bread"]
  expected_db_state:
    - query: "SELECT COUNT(*) FROM meal_records WHERE meal_type = 'breakfast'"
      expected_value: 1
```

- [ ] **Step 2: Run test to verify it ignores the new field**
Run the test to make sure the YAML change didn't break anything.

- [ ] **Step 3: Write DB state validation logic**
In `tests/eval/test_code_grader.py`, add the DB validation to the very end of `test_agent_trajectory` (below the routing checks). Add the `import sqlite3` at the top of the file if not already present.

```python
    import sqlite3
    expected_db_state = case.get("expected_db_state", [])
    if expected_db_state:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            for state_check in expected_db_state:
                cursor.execute(state_check["query"])
                result = cursor.fetchone()[0]
                expected_val = state_check["expected_value"]
                assert result == expected_val, f"DB query {state_check['query']} returned {result}, expected {expected_val}"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/eval/test_code_grader.py::test_agent_trajectory\[test_meal_breakfast\] -v
```
Expected: PASS (Note: If this fails because the LLM didn't insert the row due to the `__interrupt__` requirement, we will catch that immediately and realize the eval framework needs to simulate human approval during streaming, just like `test_meal_agent.py` does).

- [ ] **Step 5: Commit**

```bash
git add tests/eval/eval_cases.yaml tests/eval/test_code_grader.py
git commit -m "test: evaluate end-to-end task completion via sqlite db state assertions"
```