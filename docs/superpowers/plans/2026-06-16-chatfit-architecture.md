# ChatFit Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Multi-Agent Supervisor architecture using LangGraph with SQLite persistence for ChatFit.

**Architecture:** A LangGraph state graph where a Supervisor routes to specialized Sub-Agents (Training, Diet, Analyst). Short-term thread memory uses `SqliteSaver`, and long-term user profile memory uses `BaseStore` (SQLite).

**Tech Stack:** Python 3.13+, LangChain, LangGraph, SQLite, Pydantic

---

### Task 1: Define Agent State

**Files:**
- Create: `agent/state.py`
- Test: `tests/test_state.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/test_state.py
from agent.state import AgentState

def test_agent_state_keys():
    state: AgentState = {
        "messages": [],
        "next_agent": "Supervisor",
        "user_profile": {"goals": "lose weight"}
    }
    assert "messages" in state
    assert "next_agent" in state
    assert "user_profile" in state
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL (agent.state not found)

- [ ] **Step 3: Write minimal implementation**
```python
# agent/state.py
from typing import TypedDict, List
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    messages: List[BaseMessage]
    next_agent: str
    user_profile: dict
```

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add tests/test_state.py agent/state.py
git commit -m "feat: define AgentState"
```

### Task 2: Create SQLite Database Setup

**Files:**
- Create: `agent/memory/db_setup.py`
- Test: `tests/test_db_setup.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/test_db_setup.py
import os
import sqlite3
from agent.memory.db_setup import init_db

def test_init_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='workouts'")
    assert cursor.fetchone() is not None
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meals'")
    assert cursor.fetchone() is not None
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/test_db_setup.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**
```python
# agent/memory/db_setup.py
import sqlite3

def init_db(db_path: str = "chatfit_data.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            exercise TEXT,
            weight REAL,
            sets INTEGER,
            reps INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            food TEXT,
            calories INTEGER
        )
    ''')
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_db_setup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add tests/test_db_setup.py agent/memory/db_setup.py
git commit -m "feat: implement SQLite database initialization"
```

### Task 3: Implement Tool for Workout Logging

**Files:**
- Create: `agent/tools/workout_tools.py`
- Test: `tests/test_workout_tools.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/test_workout_tools.py
import sqlite3
from agent.memory.db_setup import init_db
from agent.tools.workout_tools import log_workout

def test_log_workout(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    monkeypatch.setenv("DB_PATH", db_path)
    
    result = log_workout.invoke({"exercise": "Squat", "weight": 100.0, "sets": 5, "reps": 5})
    assert "Logged Squat" in result
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT exercise, weight, sets, reps FROM workouts")
    row = cursor.fetchone()
    assert row == ("Squat", 100.0, 5, 5)
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/test_workout_tools.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**
```python
# agent/tools/workout_tools.py
import os
import sqlite3
from langchain_core.tools import tool
from pydantic import BaseModel, Field

class WorkoutSchema(BaseModel):
    exercise: str = Field(description="Name of the exercise")
    weight: float = Field(description="Weight used in kg")
    sets: int = Field(description="Number of sets")
    reps: int = Field(description="Number of repetitions per set")

@tool("log_workout", args_schema=WorkoutSchema)
def log_workout(exercise: str, weight: float, sets: int, reps: int) -> str:
    """Log a workout session to the database."""
    db_path = os.getenv("DB_PATH", "chatfit_data.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO workouts (exercise, weight, sets, reps) VALUES (?, ?, ?, ?)",
        (exercise, weight, sets, reps)
    )
    conn.commit()
    conn.close()
    return f"Logged {exercise}: {weight}kg, {sets} sets of {reps} reps."
```

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_workout_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add tests/test_workout_tools.py agent/tools/workout_tools.py
git commit -m "feat: implement log_workout tool"
```

### Task 4: Supervisor Node

**Files:**
- Create: `agent/nodes/supervisor.py`
- Test: `tests/test_supervisor.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/test_supervisor.py
from langchain_core.messages import HumanMessage
from agent.nodes.supervisor import make_supervisor_node
from agent.state import AgentState

def test_supervisor_node(mocker):
    # Mock LLM to return structured output
    mock_llm = mocker.Mock()
    mock_llm.with_structured_output.return_value.invoke.return_value = {
        "next": "TrainingAgent",
        "instructions": "Log the workout"
    }
    
    node = make_supervisor_node(mock_llm)
    state = {"messages": [HumanMessage(content="I squatted 100kg")], "next_agent": "", "user_profile": {}}
    result = node(state)
    
    assert result["next_agent"] == "TrainingAgent"
    assert "instructions" in result["messages"][-1].content
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/test_supervisor.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**
```python
# agent/nodes/supervisor.py
from typing import Literal
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, AIMessage
from agent.state import AgentState

class RouterOutput(BaseModel):
    next: Literal["TrainingAgent", "DietAgent", "AnalystAgent", "FINISH"]
    instructions: str = Field(description="Instructions for the agent or response to user")

def make_supervisor_node(llm):
    system_prompt = SystemMessage(content="""You are the Supervisor.
    Route to TrainingAgent for workouts.
    Route to DietAgent for meals.
    Route to AnalystAgent for history/data.
    Return FINISH if no routing needed.""")
    
    structured_llm = llm.with_structured_output(RouterOutput)
    
    def supervisor_node(state: AgentState):
        messages = [system_prompt] + state["messages"]
        response = structured_llm.invoke(messages)
        
        return {
            "next_agent": response["next"],
            "messages": [AIMessage(content=response["instructions"])]
        }
    return supervisor_node
```

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_supervisor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add tests/test_supervisor.py agent/nodes/supervisor.py
git commit -m "feat: implement supervisor node"
```

### Task 5: Build Graph Orchestration

**Files:**
- Create: `agent/graph.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/test_graph.py
from langgraph.graph import StateGraph
from agent.graph import create_graph

def test_create_graph(mocker):
    mock_llm = mocker.Mock()
    graph = create_graph(mock_llm)
    assert isinstance(graph, type(StateGraph(dict).compile()))
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/test_graph.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**
```python
# agent/graph.py
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.supervisor import make_supervisor_node

# Placeholder dummy nodes for compilation
def dummy_training(state: AgentState):
    return {"messages": []}

def dummy_diet(state: AgentState):
    return {"messages": []}

def dummy_analyst(state: AgentState):
    return {"messages": []}

def create_graph(llm):
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("Supervisor", make_supervisor_node(llm))
    workflow.add_node("TrainingAgent", dummy_training)
    workflow.add_node("DietAgent", dummy_diet)
    workflow.add_node("AnalystAgent", dummy_analyst)
    
    # Add edges
    workflow.add_edge(START, "Supervisor")
    
    def router(state: AgentState):
        if state["next_agent"] == "FINISH":
            return END
        return state["next_agent"]
        
    workflow.add_conditional_edges("Supervisor", router, {
        "TrainingAgent": "TrainingAgent",
        "DietAgent": "DietAgent",
        "AnalystAgent": "AnalystAgent",
        END: END
    })
    
    # Return from sub-agents back to Supervisor
    workflow.add_edge("TrainingAgent", "Supervisor")
    workflow.add_edge("DietAgent", "Supervisor")
    workflow.add_edge("AnalystAgent", "Supervisor")
    
    return workflow.compile()
```

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/test_graph.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add tests/test_graph.py agent/graph.py
git commit -m "feat: compile state graph orchestration"
```
