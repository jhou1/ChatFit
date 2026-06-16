# OpenCode Agent Instructions for ChatFit

## Architecture Context
- **Pattern**: Multi-Agent Supervisor utilizing `LangGraph`.
- **Core Principle**: Vendor-agnostic. All LLM instantiations MUST use `llm_factory/llm_factory.py`. Do not hardcode `ChatOpenAI` or `ChatAnthropic` in agent logic.
- **Node Responsibilities**:
  - `Supervisor`: Routes user intent. Does not directly invoke DB tools.
  - `TrainingAgent`: Handles fitness/workout logic and tool calling.
  - `DietAgent`: Handles nutrition/food logic and tool calling.
  - `AnalystAgent`: Reads historical data and generates summaries/visualizations.

## Memory & Storage Rules
- **Database**: Pure SQLite for business data (tables: `workouts`, `meals`).
- **Short-term Memory**: Use LangGraph `SqliteSaver` (checkpointing) for Thread history.
- **Long-term Memory**: Use LangGraph `BaseStore` (e.g., `SqliteStore`) for saving and retrieving the User Profile (preferences, injury history, goals). Do NOT use separate vector databases.
- **Data Extraction**: Always use Pydantic schemas combined with `.with_structured_output()` when extracting data for SQLite insertion to ensure type safety.

## Workflow & Commands
- **Dependency Management**: The project uses `uv`. 
- **Run the App**: Execute via `uv run python main.py`.
- **Add Packages**: Use `uv add <package>` instead of `pip install`.
- **Environment**: Ensure `.env` is loaded or API keys are passed to the `LLMConfig`.
