# ChatFit

**ChatFit** is your personal, AI-powered training and meal assistant.

By connecting ChatFit to a Telegram bot, you can seamlessly track your training volume and meals through natural chat, saving all your data securely into a local SQLite database.

The true value of tracking lies in discovery. Humans tend to repeat unseen patterns that act against their goals, but data does not lie. The core idea is that, over time, you can use a local LLM or a custom data discovery program to learn from your training and eating habits. Your data becomes a goldmine, allowing you to uncover insights such as: performance breakthroughs, strength development patterns, root causes of injuries and setbacks, and how weight changes correlate with your daily activities.

ChatFit is completely unopinionated about your choice of LLM. You can plug in your favorite provider or use local LLMs when data privacy is your top concern.

# Getting Started
## Integrating with Telegram

1. `export TELEGRAM_BOT_TOKEN="your-bot-token-from-botfather"`
2. `cp .env.example .env`, open `.env` with a text editor, and then enter the values of the required fields.
3. Configure `docker-compose.yml` to mount your db file path and RAG directory path.
4. Spin up the service:
```bash
podman compose up -d

# or alternatively
docker compose up -d
```

## Evaluation Framework

ChatFit features a dual-pipeline Agent Evaluation Framework built around local execution and **Langfuse Cloud** tracing.

1. **Code Grader (CI/Integration)**
   - Defined in `tests/eval/eval_cases.yaml`, these tests run the agent locally against fixed inputs.
   - The custom Pytest runner (`test_code_grader.py`) analyzes the agent's trajectory and deterministically asserts that the expected tool calls (e.g., `log_meal`) were made with the correct arguments.
   - Run it with: `uv run pytest tests/eval -v`

2. **LLM-as-a-Judge (Production Quality)**
   - A standalone script (`scripts/llm_judge.py`) fetches real execution traces from Langfuse Cloud.
   - It uses an LLM evaluator to score the conversational tone and RAG context quality (1-5), pushing those metrics back into the Langfuse UI.
   - Run it with: `uv run python scripts/llm_judge.py <langfuse_trace_id>`

Roadmap
- LLM provider agnostic configuration
- Deployment on container-native infrastructure
- Connecting to WeChat
- ... and more
