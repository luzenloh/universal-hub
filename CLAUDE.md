# CLAUDE.md — AI Portfolio Projects

Инструкции для Claude Code при работе с этим репозиторием.

## Цель репозитория

5 рабочих AI-проектов для портфолио Junior AI Developer / Vibe Coder.
Каждый проект деплоится, имеет demo-ссылку и README со скриншотом.

---

## Структура проектов

```
cCODES/
├── docchat/      # RAG-чатбот по PDF/TXT (Streamlit + LangChain + FAISS)
├── agentflow/    # AI-агент с tool calling (Claude API + Streamlit)
├── datalens/     # NL → data analysis + charts (Pandas + Plotly + Streamlit)
├── telegramai/   # Telegram бот с памятью (python-telegram-bot + SQLite)
└── autobrief/    # FastAPI сервис AI-автоматизации (FastAPI + HTMX)
```

Каждый проект содержит:
- `app.py` / `main.py` / `bot.py` — точка входа
- `prompts.py` — все промпты вынесены отдельно (демонстрирует prompt engineering)
- `requirements.txt` — зависимости
- `Dockerfile` — для деплоя
- `README.md` — с архитектурой, скриншотом, инструкцией

---

## Стек

- **LLM**: Claude API (`claude-opus-4-6`) как основная модель
- **UI**: Streamlit (быстрые прототипы), FastAPI + HTMX (AutoBrief)
- **RAG**: LangChain + FAISS + OpenAI Embeddings
- **Data**: Pandas + Plotly
- **Telegram**: python-telegram-bot (async)
- **DB**: SQLite (TelegramAI)
- **Deploy**: Hugging Face Spaces, Railway, Render

---

## Правила работы

### Код
- Простой, читаемый код — работодатель должен понять за 30 секунд
- Не усложнять: если задача решается 10 строками — не делать 50
- Каждый проект независим (свой `requirements.txt`, свой `.env`)
- Секреты только через переменные окружения (`.env` файл, никогда не в коде)
- Использовать `python-dotenv` для загрузки `.env`
- Всегда создавать `.env.example` рядом с `.env`

### Промпты
- Все промпты хранятся в `prompts.py` каждого проекта
- Промпты документируются комментариями — показывает понимание prompt engineering

### Безопасность
- Никаких `eval()` без sandbox (исключение: DataLens с ограниченным `exec`)
- Валидация пользовательского ввода на входе в API
- API-ключи только из `os.getenv()`

---

## Git-конвенции

### Формат коммитов (Conventional Commits)

```
<type>(<scope>): <description>

[optional body]
```

**Типы:**
- `feat` — новая функция
- `fix` — исправление бага
- `docs` — README, комментарии
- `refactor` — рефакторинг без изменения поведения
- `chore` — настройка окружения, зависимости, Dockerfile

**Scope** — имя проекта: `docchat`, `agentflow`, `datalens`, `telegramai`, `autobrief`, `root`

**Примеры:**
```
feat(docchat): add FAISS vector index and RAG pipeline
feat(docchat): add Streamlit chat UI with file upload
feat(agentflow): implement tool calling agent loop
fix(datalens): fix sandbox exec for plotly charts
docs(telegramai): add README with architecture diagram
chore(root): add .gitignore and CLAUDE.md
```

### Стратегия коммитов
- Коммит после каждого рабочего шага (не "сохраняй всё в конце")
- Каждый коммит должен оставлять проект в **рабочем состоянии**
- Один коммит = одна логическая единица изменений
- Перед коммитом проверить: нет ли в стейдже `.env` или `__pycache__`

### Тестирование перед коммитом
- **Никогда не коммитить непроверенный код**
- Порядок: написали код → установили зависимости → запустили → проверили руками → коммит
- Для Telegram-ботов: запустить локально, пройтись по всем командам вручную
- Для Streamlit/FastAPI приложений: запустить локально, проверить основные сценарии
- Если что-то не работает — сначала чиним, потом коммитим

---

## Деплой

| Проект | Платформа | Конфиг |
|--------|-----------|--------|
| DocChat | Hugging Face Spaces | `README.md` с `sdk: streamlit` |
| AgentFlow | Railway | `Dockerfile` |
| DataLens | Hugging Face Spaces | `README.md` с `sdk: streamlit` |
| TelegramAI | Railway | `railway.json` + `Dockerfile` |
| AutoBrief | Render | `render.yaml` |

---

## Переменные окружения

Все проекты ожидают в `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...        # только DocChat (embeddings)
TELEGRAM_BOT_TOKEN=...       # только TelegramAI
```
