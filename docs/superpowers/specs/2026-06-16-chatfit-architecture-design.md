# ChatFit Architecture Design

## 1. Overview
ChatFit is a multi-agent AI system built to track fitness and dietary habits. The system utilizes LangGraph for state management and agent orchestration, ensuring the application is vendor-agnostic and maintains long-term user memory without requiring complex vector databases.

## 2. Core Architecture: Multi-Agent Supervisor
The system is built on a routing architecture where a single Supervisor agent delegates tasks to specialized sub-agents. 

### Node Responsibilities
- **Supervisor Agent**: The primary entry point. Analyzes user intent and routes to the appropriate expert. Handles simple conversational inputs directly.
- **Training Agent**: Specialized in processing workout data. Has access to tools for recording and querying workout routines, weights, and sets.
- **Diet Agent**: Specialized in nutrition. Parses food inputs, estimates caloric and macronutrient values, and records them.
- **Analyst Agent**: Activated when users request historical reviews or visual reports. Reads aggregate data to identify trends or weaknesses.

## 3. Data & Memory Strategy
- **Business Data**: Stored in a standalone SQLite database (`chatfit_data.db`). Contains structured tables such as `workouts` and `meals`.
- **Short-Term Memory (Thread Level)**: Managed via LangGraph's `SqliteSaver` to maintain conversational context within a single session.
- **Long-Term Memory (User Profile)**: Managed via LangGraph's `BaseStore` (backed by SQLite). Stores user goals, dietary restrictions, and injury history, injected into the state by the Supervisor at the start of interactions.
- **Data Extraction**: Enforced using Pydantic schemas combined with LLM structured outputs to ensure database insertion reliability.

## 4. Implementation Path
1. **State Definition (`agent/state.py`)**: Define `AgentState` containing messages, routing targets, and the user profile.
2. **Tools Layer (`agent/tools/`)**: Implement Python functions for SQLite CRUD operations (`log_workout`, `log_meal`, `query_history`) with strict type validation.
3. **Memory Layer (`agent/memory/`)**: Initialize SQLite connections for `SqliteSaver` and `BaseStore`.
4. **Agent Nodes (`agent/nodes/`)**: Build the Supervisor and Sub-Agent logic, binding tools to the sub-agents.
5. **Graph Orchestration (`agent/graph.py`)**: Wire nodes using LangGraph's `StateGraph`, defining conditional edges based on Supervisor decisions.
