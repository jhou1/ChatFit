# ChatFit

**ChatFit** is your personal, AI-powered training and meal assistant.

By connecting ChatFit to a Telegram bot, you can seamlessly track your training volume and meals through natural chat, saving all your data securely into a local SQLite database.

The true value of tracking lies in discovery. Humans tend to repeat unseen patterns that act against their goals, but data does not lie. The core idea is that, over time, you can use a local LLM or a custom data discovery program to learn from your training and eating habits. Your data becomes a goldmine, allowing you to uncover insights such as: performance breakthroughs, strength development patterns, root causes of injuries and setbacks, and how weight changes correlate with your daily activities.

ChatFit is completely unopinionated about your choice of LLM. You can plug in your favorite provider or use local LLMs when data privacy is your top concern.

# Getting Started
## Integrating with Telegram

1. `export TELEGRAM_BOT_TOKEN="your-bot-token-from-botfather"`
2. `cp .env.example .env`, open `.env` with a text editor, and then enter the values of the required fields.
3. Spin up the service:
```bash
podman compose up -d

# or alternatively
docker compose up -d
Roadmap
- LLM provider agnostic configuration
- Deployment on container-native infrastructure
- Connecting to WeChat
- ... and more
