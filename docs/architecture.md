The system follows a multi-agent orchestration pattern using LangGraph, persisting data locally in SQLite, and tracking agent trajectories via Langfuse.

```mermaid
graph TD
    %% Entry Points
    User((User)) -->|Sends Message| TelegramBot[Telegram Bot]

    %% Bot & API Layer
    TelegramBot -->|Forwards Update| API[FastAPI App]
    API -->|Triggers| Supervisor[Supervisor Agent]

    %% Agent Layer (LangGraph)
    subgraph LangGraph Orchestration
        Supervisor -->|Delegates| TrainingAgent[Training Agent]
        Supervisor -->|Delegates| MealAgent[Meal Agent]
        Supervisor -->|Delegates| InsightsAgent[Insights Agent]

        TrainingAgent -->|Returns Result| Supervisor
        MealAgent -->|Returns Result| Supervisor
        InsightsAgent -->|Returns Result| Supervisor
    end

    %% Data & Tools Layer
    TrainingAgent -.->|SQL Reads/Writes| SQLite[(SQLite DB)]
    MealAgent -.->|SQL Reads/Writes| SQLite
    InsightsAgent -.->|Queries Data| SQLite
    InsightsAgent -.->|RAG| VectorStore[(Vector Store / RAG)]

    %% Evaluation Pipeline
    subgraph Evaluation Framework
        SQLite -.->|Traces| Langfuse[Langfuse]
        CodeGrader[Pytest Code Grader] -.->|Checks Actions| Langfuse
        LLMJudge[LLM Judge] -.->|Scores Responses| Langfuse
    end
```
