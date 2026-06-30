ChatFit 是一个根据你的当前情况和目标，帮助你记录训练和饮食记录的 Agent

# Features
- 数据才是你的核心资产，你的训练数据和饮食记录会被结构化存储
- 任何 Provider：OpenAI，Anthropic, DeepSeek 或是本地部署的 LLM
- 持久化记忆，记忆会根据用户喜好持续化更
- 通过数据进行回顾和总结，寻找训练和饮食中存在的盲区、短板
- 帮助你规划和实现你的目标


# Integrating to Telegram

1. export TELEGRAM_BOT_TOKEN="your-bot-token-from-botfather"
2. Start the API Server (in one terminal)
uv run uvicorn api:app --reload
3. Start the Telegram Bot (in a second terminal)
uv run python bot.py

# Getting Started
## Create Telegram Bot

## Prepare your LLM provider's API key

## Run
1. Copy the `.env.example` to `.env`, edit `.env` and add your API key for LLM and Telegram bot
2. Optional: configure LangSmith to trace your agent steps
3. Spin up your services with `podman compose up`.
