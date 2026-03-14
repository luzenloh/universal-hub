# CLAUDE.md — AI Backend Portfolio

Инструкции для Claude Code при работе с этим репозиторием.

## Цель репозитория

5 production-style AI backend проектов для портфолио AI / Backend Developer.

Каждый проект демонстрирует:
- backend архитектуру (не demo-style, а как реальный сервис)
- интеграцию с LLM через абстракции
- работу с векторными БД и RAG
- data processing pipelines
- agent orchestration
- PostgreSQL + Docker Compose деплой

---

## Структура репозитория

```
cCODES/
├── rag-service/          # Production RAG backend (FastAPI + PostgreSQL + pgvector)
├── agent-orchestrator/   # Multi-agent LLM система (planner / executor / critic)
├── data-ai-engine/       # NL → data analysis engine (Pandas + sandbox + LLM)
├── llm-evaluator/        # LLM-as-a-judge evaluation system
└── ai-automation/        # AI workflow automation backend (job queue + task pipelines)
```

---

## Структура каждого проекта

```
project/
├── app/
│   ├── api/
│   │   └── routes.py
│   ├── services/          # бизнес-логика
│   ├── repositories/      # работа с БД
│   ├── models/
│   │   ├── schemas.py     # Pydantic
│   │   └── db_models.py   # SQLAlchemy
│   ├── core/
│   │   ├── config.py
│   │   └── dependencies.py
│   └── llm/
│       ├── client.py
│       └── prompts.py     # все промпты здесь
├── main.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

Роуты содержат только HTTP-логику. Бизнес-логика — в сервисах. Работа с БД — в репозиториях.

---

## Стек

### Backend
- Python 3.11+
- FastAPI + Pydantic
- SQLAlchemy (async)

### Database
- PostgreSQL (основная БД)
- pgvector / FAISS (vector store)

### AI / ML
- Claude API (`claude-opus-4-6`) — основная LLM
- SentenceTransformers — embeddings
- LangChain — только там, где реально упрощает (RAG pipeline)
- Pandas + NumPy — data processing

### Infra
- Docker + Docker Compose
- async FastAPI

---

## Правила кода

### Архитектура
- Модульная структура — никаких монолитных `app.py` с бизнес-логикой внутри роутов
- Dependency injection через FastAPI `Depends`
- Сервисный слой изолирован от транспортного
- Репозитории изолируют работу с БД

### Качество
- Type hints везде — функции, переменные, возвращаемые значения
- Логирование через `logging` (не `print`)
- Обработка ошибок: явные исключения с понятными сообщениями
- Каждый проект независим: свой `requirements.txt`, свой `.env`

### Промпты
- Все промпты в `app/llm/prompts.py`
- Промпты документируются комментариями — показывает понимание prompt engineering
- LLM вызывается только через `app/llm/client.py`, не напрямую из роутов/сервисов

### Безопасность
- Секреты только через `os.getenv()` / `pydantic-settings`
- Никакого `eval()` без sandbox
- Валидация входных данных через Pydantic схемы на входе в API
- Никогда не коммитить `.env`

---

## Git-конвенции

### Формат (Conventional Commits)

```
<type>(<scope>): <description>
```

**Типы:**
- `feat` — новая функция
- `fix` — исправление бага
- `docs` — README, комментарии
- `refactor` — рефакторинг без изменения поведения
- `chore` — зависимости, Dockerfile, конфиги

**Scope** — имя проекта: `rag-service`, `agent-orchestrator`, `data-ai-engine`, `llm-evaluator`, `ai-automation`, `root`

**Примеры:**
```
feat(rag-service): implement async document ingestion pipeline
feat(rag-service): add pgvector retrieval with metadata filtering
feat(agent-orchestrator): implement planner agent and tool registry
feat(data-ai-engine): add sandboxed pandas execution engine
feat(llm-evaluator): implement LLM-as-a-judge scoring pipeline
fix(rag-service): fix embedding batch size causing OOM
chore(root): add .gitignore and CLAUDE.md
```

### Стратегия коммитов
- Коммит после каждого рабочего шага
- Каждый коммит оставляет проект в **запускаемом состоянии**
- Один коммит = одна логическая единица (один сервис, один эндпоинт, один pipeline)
- Перед коммитом: нет `.env`, нет `__pycache__`, нет секретов в коде

### Тестирование перед коммитом
- **Не коммитить непроверенный код**
- Порядок: написали → `docker-compose up` → проверили endpoint → коммит
- Если что-то не работает — сначала чиним

---

## Деплой

Каждый проект запускается через:

```bash
docker-compose up
```

`docker-compose.yml` содержит минимум: backend + postgres + (vector db если нужен).

---

## Переменные окружения

`.env.example` обязателен в каждом проекте:

```
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=

ANTHROPIC_API_KEY=
OPENAI_API_KEY=        # только там, где нужны OpenAI embeddings
```
