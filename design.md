# ChatFit Directory Structure & Architecture Guide

## 1. Directory Structure

To manage a complex LangGraph Multi-Agent project and avoid circular dependencies, follow this strict top-down directory structure:

```text
ChatFit/
├── main.py                     # Entry point: Initializes Graph and Memory, starts the run loop
├── llm_factory/                
│   └── llm_factory.py          # Unified LLM initialization (Vendor-agnostic)
├── agent/
│   ├── __init__.py
│   ├── state.py                # Global AgentState definition
│   ├── graph.py                # Graph orchestration (Nodes + Edges)
│   ├── storage/
│   │   ├── __init__.py
│   │   └── db.py               # SQLite connection & table setup to save diet and training records
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── workout_tools.py    # @tool definitions for workouts
│   │   └── diet_tools.py       # @tool definitions for meals
│   └── nodes/
│       ├── __init__.py
│       ├── supervisor.py       # Supervisor routing logic
│       ├── training_agent.py   # Training Agent logic
│       └── diet_agent.py       # Diet Agent logic
```

## 2. Core Components & Dependency Flow

**Dependency Rule**: `State` -> `Tools` -> `Nodes` -> `Graph` -> `Main`. Never import `nodes` or `graph` into `state.py` or `tools`.

### Level 1: State (`agent/state.py`)
No dependencies. Defines the global graph state. `add_messages` automatically handles appending new messages so manual reconciling is not needed.
```python
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next_agent: str
    user_profile: dict
```

### Level 2: Tools (`agent/tools/`)
Depends only on basic libraries or database logic. Never imports `state` or `nodes`.
```python
import sqlite3
from langchain_core.tools import tool
from pydantic import BaseModel, Field

class WorkoutSchema(BaseModel): ...

@tool("log_workout", args_schema=WorkoutSchema)
def log_workout(exercise: str, weight: float) -> str:
    # DB interaction logic
    return "success"
```

### Level 3: Nodes (`agent/nodes/`)
Depends on `State`, `Tools`, and `LLM Factory`. Uses factory functions/closures to inject dependencies like `llm_config` cleanly.
```python
from agent.state import AgentState
from agent.tools.workout_tools import log_workout
from llm_factory.llm_factory import create_chat_model

def make_training_node(llm_config):
    llm = create_chat_model(llm_config)
    llm_with_tools = llm.bind_tools([log_workout])
    
    def node(state: AgentState):
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]} # Returns incremental update only
        
    return node
```

### Level 4: Graph Orchestration (`agent/graph.py`)
Depends on `State` and `Nodes`. Acts as the assembly line, wiring nodes with conditional routing edges.
```python
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.supervisor import make_supervisor_node
from agent.nodes.training_agent import make_training_node

def create_graph(llm_config):
    builder = StateGraph(AgentState)
    
    builder.add_node("Supervisor", make_supervisor_node(llm_config))
    builder.add_node("TrainingAgent", make_training_node(llm_config))
    
    builder.add_edge(START, "Supervisor")
    
    def router(state: AgentState):
        return END if state["next_agent"] == "FINISH" else state["next_agent"]
        
    builder.add_conditional_edges("Supervisor", router)
    builder.add_edge("TrainingAgent", "Supervisor")
    
    return builder
```

### Level 5: Entry Point (`main.py`)
Depends on `Graph` and Memory utilities. Injects Checkpointer (Short-term) and Store (Long-term) into the compiled graph.
```python
from agent.graph import create_graph
from llm_factory.llm_factory import LLMConfig
from langgraph.checkpoint.sqlite import SqliteSaver

def main():
    llm_config = LLMConfig(...)
    builder = create_graph(llm_config)
    
    with SqliteSaver.from_conn_string("chatfit_checkpoints.db") as checkpointer:
        app = builder.compile(checkpointer=checkpointer)
        # Execute app.stream or app.invoke ...
```

## 3. Key Takeaways
1. **Auto-Reconciliation:** LangGraph's `Annotated[..., add_messages]` natively aggregates `messages` returned by Sub-Agents.
2. **Factory Pattern:** Use functions like `make_training_node()` to avoid global LLM state and ease unit testing.
3. **Strict Boundaries:** Sub-Agents are pure functions taking a State and returning a partial State (e.g., `{"messages": [new_message]}`). They do not handle their own loops; the `StateGraph` edges handle moving data back to the Supervisor.
