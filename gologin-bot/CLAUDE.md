# CLAUDE.md — gologin-bot

## Роль

Senior-разработчик: анализ перед кодом, разбивка на слои (БД → сервис → API → UI), не молчать если подход неверный.

---

## Суть

Telegram-бот + локальный агент для управления рабочими сменами MassMO.

**Hub** — центральный сервер: Telegram-бот, БД (SQLite), Hub API для агентов.
**Agent** — на Mac сотрудника: запускает GoLogin-профили, извлекает JWT, управляет выплатами через MassMO REST API. Дашборд на `localhost:8081`.

---

## Структура

```
gologin-bot/
├── hub_main.py / agent_main.py      # точки входа
├── hub/
│   ├── core/config.py               # BOT_TOKEN, ADMIN_USERNAME, HUB_SECRET, ...
│   ├── db/models.py                 # Folder, Agent, User, Schedule
│   ├── db/repository.py             # FolderRepo, AgentRepo, UserRepo, ScheduleRepo
│   ├── db/base.py                   # async engine + init_db (safe ALTER TABLE migrations)
│   ├── handlers/shift.py            # выбор папки → запуск агента; pin/unpin сообщения
│   ├── handlers/schedule.py         # /schedule — недельный график
│   ├── handlers/admin.py            # /folders, /sync, /agents, /team
│   ├── handlers/common.py           # /start
│   ├── keyboards/builder.py
│   ├── middlewares/db.py
│   ├── api/routes.py                # /hub/register, /hub/heartbeat (pin + stats + notify)
│   └── services/sync.py             # GoLogin Cloud → SQLite
├── agent/
│   ├── core/config.py               # HUB_URL, HUB_SECRET, AGENT_ID, OWNER_TELEGRAM_ID
│   └── services/hub_client.py + tunnel.py
├── bot/services/                    # используется агентом
│   ├── massmo_api.py                # MassmoClient (default sender bank = tinkoff)
│   ├── window_agent.py              # WindowAgent state machine
│   ├── orchestrator.py              # singleton + JWT-кэш
│   ├── gologin.py + massmo_actions.py + ws_manager.py
└── web/
    ├── app.py                       # FastAPI factory + lifespan
    ├── api/routes.py + ws.py + agent_routes.py
    ├── models/schemas.py            # WindowState, WindowStatus, CommandType, PayoutData
    └── static/index.html            # SPA (vanilla JS, тёмная/светлая тема)
```

---

## Поток смены

```
Telegram → shift:launch_folder
  → find agent by owner_telegram_id (assigned_folder_id = NULL)
  → assign_folder (atomic) + assign_agent_to_folder
  → reset_session_stats(agent)
  → POST /agent/start_shift → Agent запускает профили M1..MN
  → pin_chat_message с live-статусом смены
  → heartbeat каждые 10s → Hub обновляет закреплённое сообщение (throttle 5s)

shift:release
  → unpin + clear_pinned_message
  → release_folder + stop_shift(agent) + release_agent
```

---

## Модели БД

**Agent** (ключевые поля):
`agent_id`, `public_url`, `local_url`, `owner_telegram_id`, `assigned_folder_id`, `notify_chat_id`,
`pinned_message_id`, `pinned_chat_id`,
`session_payout_count`, `active_payout_count`, `searching_count`, `last_payout_at`

**Folder**: `gologin_id`, `name`, `main_profile_id`, `numbered_profile_ids` (JSON), `massmo_secrets` (JSON), `is_free`, `assigned_to`, `assigned_agent_id`

**Schedule**: `telegram_id`, `week_start`, `days` (JSON), свойство `.days_dict`. Неделя = следующий Пн–Вс.

---

## WindowAgent — state machine

```
CONNECTING → IDLE ↔ SEARCHING → ACTIVE_PAYOUT → PAID
                              → EXPIRING → extend → ACTIVE_PAYOUT
DISABLED / ERROR (backoff 2→60s) / STOPPED
```
Polling: IDLE=15s, SEARCHING=5s, ACTIVE_PAYOUT/EXPIRING=3s.
Default sender bank: `tinkoff` (установлен в `MassmoClient.__init__` и SELECT_SENDER_BANK fallback).
При входе в ACTIVE_PAYOUT дашборд автоматически вызывает `SELECT_SENDER_BANK tinkoff`.

---

## Hub API heartbeat (`/hub/heartbeat`)

- Обновляет `last_seen` агента
- Diff статусов → уведомления в Telegram: только EXPIRING (auto-delete 60s), VERIFICATION_FAILED, ERROR
- Обновляет закреплённое сообщение (throttle 5s, catch "not modified" / "not found")
- Обновляет статистику: `active_payout_count`, `searching_count`, `session_payout_count`

---

## Дашборд (`web/static/index.html`)

- Тёмная/светлая тема, toggle в navbar, сохраняется в `localStorage`
- Карточки с цветной полоской по статусу, сортировка по приоритету (EXPIRING → ACTIVE → ...)
- Staleness dot: жёлтый >30s, красный >120s
- Таймер обратного отсчёта в шапке карточки (красный < 5 мин)
- Clipboard-кнопка у реквизита получателя
- Paste (Cmd+V) → загрузка чека из буфера, picker если несколько активных
- Session counter в navbar (sessionStorage, только по `window_update`)

---

## Telegram-команды

| Команда | Кто | Что |
|---|---|---|
| `/start` | все | регистрация пользователя |
| `/schedule` | все | недельный график смен |
| `/folders` | admin | список папок с состоянием |
| `/sync` | admin | синхронизация GoLogin Cloud → DB |
| `/agents` | admin | список агентов |
| `/team` | admin | live-статистика команды + кнопка обновить |
| `/set_secrets` | admin | `/set_secrets <folder_id> <s1> <s2>...` |

---

## Переменные окружения

**`.env.hub`:** `BOT_TOKEN`, `ADMIN_USERNAME` (без @), `GOLOGIN_API_TOKEN`, `HUB_SECRET`, `HUB_HOST=127.0.0.1`, `HUB_PORT=8082`, `DATABASE_URL=sqlite+aiosqlite:///./hub.db`

**`.env.agent`:** `HUB_URL`, `HUB_SECRET`, `AGENT_ID`, `OWNER_TELEGRAM_ID`, `AGENT_PORT=8081`, `AGENT_HOST=127.0.0.1`

---

## Запуск

```bash
# Hub:
pkill -f hub_main; lsof -ti :8082 | xargs kill -9 2>/dev/null
nohup python3.10 hub_main.py >> /tmp/hub.log 2>&1 &

# Agent:
nohup .venv/bin/python agent_main.py >> /tmp/massmo-agent.log 2>&1 &
```

---

## Git

`<type>(gologin-bot): <description>` — типы: `feat`, `fix`, `refactor`, `chore`
Не коммитить: `.env*`, `__pycache__`, `*.db`, `massmo_jwt_cache.json`

---

## Не трогать

`bot/` (кроме `bot/services/`) и `main.py` — legacy Phase 1.
