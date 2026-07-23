# ChatFit Architecture

The system follows a multi-agent orchestration pattern using LangGraph, persists
business data and conversation checkpoints locally, and optionally exports Agent
trajectories to Langfuse.

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

    %% Cross-cutting quality systems
    subgraph Quality Systems
        API -.->|Callback Traces| Langfuse[Langfuse]
        Supervisor -.->|Graph Events| CodeGrader[Pytest Code Grader]
        CodeGrader -.->|Asserts Final State| TestDB[(Test SQLite)]
        LLMJudge[LLM Judge] -.->|Writes Scores| Langfuse
    end
```

## Cross-cutting designs

- [Agent Evaluation](evaluation.md) defines datasets, graders, quality metrics,
  release gates, and the production feedback loop.
- [Agent Observability](observability.md) defines trace identity, span hierarchy,
  execution-path instrumentation, metrics, alerts, and privacy controls.
- [Quality and Verification](quality.md) defines the local static-analysis and
  test gates.

Evaluation and observability share the same correlation model. A test or
production request is represented by one `trace_id`; related turns share a
`session_id/thread_id`; evaluation traffic also carries a `run_id` and
`case_id`. Observability records what happened, while Evaluation decides whether
that behavior was acceptable.
