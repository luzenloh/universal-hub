# TelegramAI

A public Telegram bot with session-based memory, powered by Llama 3.3 70B via Groq (free tier).

## Features

- Answers any question in natural language
- Remembers conversation context within a session
- Auto-resets after 2 hours of inactivity
- `/clear` to manually reset history
- `/summary` to get a brief recap of the conversation

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | List all commands |
| `/clear` | Clear conversation history |
| `/summary` | Summarize the current conversation |

## Architecture

```
bot.py       — Telegram handlers and main loop
memory.py    — In-memory session store (dict per user_id, 2h timeout)
prompts.py   — System prompt and summary prompt
```

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) and get your token
2. Get a free Groq API key at [console.groq.com](https://console.groq.com)
3. Copy `.env.example` to `.env` and fill in the values

```bash
pip install -r requirements.txt
python bot.py
```

## Deploy (Railway)

```bash
railway up
```

Set environment variables in Railway dashboard:
- `TELEGRAM_BOT_TOKEN`
- `GROQ_API_KEY`

## Stack

- [python-telegram-bot](https://python-telegram-bot.org/) — async Telegram API wrapper
- [Groq](https://groq.com/) — free, fast LLM inference
- Llama 3.3 70B — the underlying language model
