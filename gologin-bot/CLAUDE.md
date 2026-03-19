# CLAUDE.md — gologin-bot

## Роль Claude в этом проекте

Я выступаю как **senior-разработчик**, которому дают ТЗ. Это означает:

- **Перед кодом — анализ.** Для нетривиальных задач сначала предлагаю варианты решения с trade-off'ами, не бросаюсь сразу писать.
- **Не соглашаюсь молча.** Если подход очевидно неверный или есть лучше — говорю прямо и объясняю почему.
- **Разбиваю на подзадачи.** БД → сервис → API → UI — каждый слой отдельно, в правильном порядке.
- **Думаю как специалисты узкого профиля.** Backend — чистая бизнес-логика, frontend — UX и отзывчивость, всё тестируется перед коммитом.
- **Финальный результат — рабочий продукт.** Не "должно работать", а реально запускается и проверено.

---

## Суть проекта

Telegram-бот + локальный агент для управления рабочими сменами MassMO.

**Hub** (центральный сервер) — Telegram-бот + БД. Операторы выбирают папку, бот командует агентом.

**Agent** (у каждого сотрудника на Mac) — запускает GoLogin-профили, извлекает JWT, управляет ими через MassMO REST API. Дашборд (`localhost:8081`) открывается в TM-браузере.

---

## Архитектура

```
gologin-bot/
├── hub_main.py                  # Hub entry point (bot + hub API)
├── agent_main.py                # Agent entry point (dashboard + agent API + tunnel)
│
├── hub/                         # Центральный сервер
│   ├── core/config.py           # BOT_TOKEN, ADMIN_USERNAME, GOLOGIN_API_TOKEN
│   ├── db/
│   │   ├── models.py            # Folder, Agent, User, Schedule
│   │   ├── repository.py        # FolderRepository, AgentRepository, UserRepository, ScheduleRepository
│   │   └── base.py              # async engine + init_db (safe migrations)
│   ├── handlers/
│   │   ├── common.py            # /start (авторегистрация User)
│   │   ├── shift.py             # выбор папки → запуск агента → уведомления
│   │   ├── schedule.py          # /schedule — недельный график смен
│   │   └── admin.py             # /folders, /sync, /agents
│   ├── keyboards/builder.py
│   ├── middlewares/db.py        # DbSessionMiddleware
│   ├── api/routes.py            # /hub/register, /hub/heartbeat, /hub/notify
│   └── services/sync.py        # GoLogin Cloud → SQLite sync
│
├── agent/                       # Локальный агент (на Mac сотрудника)
│   ├── core/config.py           # HUB_URL, HUB_SECRET, AGENT_ID, OWNER_TELEGRAM_ID, AGENT_PORT
│   └── services/
│       ├── hub_client.py        # register(), heartbeat_loop(), send_heartbeat()
│       └── tunnel.py            # start_tunnel(), keep_tunnel_alive() — Cloudflare
│
├── bot/services/                # Общие сервисы (используются агентом)
│   ├── gologin.py               # GoLoginService (Desktop :36912)
│   ├── massmo_actions.py        # extract_jwt(ws_url), open_url_in_browser(ws_url, url)
│   ├── massmo_api.py            # MassmoClient — полный REST API клиент
│   ├── window_agent.py          # WindowAgent — state machine на asyncio.Task
│   ├── orchestrator.py          # singleton, управляет агентами, JWT-кэш
│   └── ws_manager.py            # WebSocket broadcast
│
└── web/                         # Dashboard (запускается агентом)
    ├── app.py                   # FastAPI factory + lifespan (restore_from_cache)
    ├── api/
    │   ├── routes.py            # REST: /windows, /windows/{id}/command, /upload
    │   ├── ws.py                # WebSocket /ws (ping каждые 25s)
    │   └── agent_routes.py      # /agent/start_shift, /agent/stop_shift, /agent/status
    ├── models/schemas.py        # WindowState, WindowStatus, CommandType, PayoutData
    └── static/index.html        # SPA оператора (vanilla JS)
```

---

## Поток смены (Hub → Agent)

```
Оператор в Telegram
  → выбирает папку + количество профилей
  → Hub находит свободный агент (owner_telegram_id совпадает, assigned_folder_id = NULL)
  → Hub POST /agent/start_shift → Agent
      Agent запускает TM-браузер (GoLogin Desktop)
      Agent запускает M1…MN последовательно → извлекает JWT → закрывает браузеры
      Agent создаёт WindowAgent для каждого Mx (polling MassMO REST API)
      Agent открывает dashboard в TM-браузере
  → Hub получает уведомления от агента (ACTIVE_PAYOUT, PAID, ERROR) → пересылает в Telegram

Оператор нажимает "Завершить смену"
  → Hub POST /agent/stop_shift → Agent
      Agent: DELETE /users/tokens для каждого агента (logout)
      Agent: удаляет massmo_jwt_cache.json
  → Hub освобождает папку и агента в БД
```

---

## Ключевые концепции

### Модели БД (hub/)

**Folder** — GoLogin папка (слот смены)
- `gologin_id`, `name`, `main_profile_id`, `numbered_profile_ids` (JSON), `massmo_secrets` (JSON)
- `is_free`, `assigned_to` (telegram_id), `assigned_agent_id`

**Agent** — зарегистрированный локальный агент
- `agent_id`, `public_url` (cloudflare), `local_url`, `last_seen`, `is_active`
- `owner_telegram_id`, `assigned_folder_id`, `notify_chat_id`

**User** — автоматически создаётся при `/start`
- `telegram_id`, `username`, `first_name`

**Schedule** — недельный график смен
- `telegram_id`, `display_name`, `week_start` (date), `days` (JSON)
- `days` формат: `{"2026-03-24": {"shift": "day"|"night"|"off", "direction": "pay_out"|"pay_in"|"matching"|null}}`

### JWT-кэш (`massmo_jwt_cache.json`)
```json
{"M1": {"jwt": "eyJ..."}, "M2": {"jwt": "eyJ..."}}
```
JWT живёт ~1 год. При `stop_agents()` файл удаляется. Кэш защищён `asyncio.Lock`.

### WindowAgent — state machine
```
CONNECTING → IDLE ↔ SEARCHING → ACTIVE_PAYOUT → PAID
                              → EXPIRING (can_prolong=True) → extend → ACTIVE_PAYOUT
DISABLED  — выплатчик отключён
ERROR     — exponential backoff (2→4→8→…→60s)
```
Polling: IDLE=15s, SEARCHING=5s, ACTIVE_PAYOUT=3s, EXPIRING=3s, ERROR=10s

### MassMO REST API (`https://findssnet.io/api/massmo/v1`)
- Auth: `POST /users/tokens {"secret":"..."}` → JWT / `DELETE /users/tokens`
- State: `GET /executor` → `{state, payout_state, min_amount, max_amount}`
- Active order: `GET /payout_orders/active`
- Search: `GET /executor/enqueue` / `GET /executor/dequeue`
- Extend: `POST /payout_orders/{id}/prolong`
- Receipt: `POST /payout_orders/{id}/verification`, field: `proofs[]`
- Settings: `PATCH /executor` → `{min_amount, max_amount, bank_names, accepts_sbp, ...}`

### Cloudflare Tunnel (agent/)
`keep_tunnel_alive(port, on_new_url)` — supervisor loop: перезапускает cloudflared при падении, вызывает `on_new_url` с новым URL → Hub re-registration. Поддерживает async callback.

### Agent self-healing
- PID-файл `/tmp/massmo-agent.pid` — один экземпляр
- `_free_port(port)` — освобождает порт при старте через `lsof`
- Heartbeat каждые 10s → Hub помечает агент `is_active=True`

---

## /schedule — граfик смен

Callback-схема:
```
sched:view                           — обновить главный вид
sched:day:{YYYY-MM-DD}               — конфигуратор дня
sched:shift:{YYYY-MM-DD}:{shift}     — выбрать тип смены
sched:dir:{YYYY-MM-DD}:{shift}:{dir} — выбрать направление → назад на главный
sched:template                       — скопировать прошлую неделю
sched:team                           — расписание команды
sched:submit                         — сохранить в БД
```

Черновики хранятся в памяти `_drafts: dict[int, dict]`. При открытии — восстанавливаются из БД если есть. Неделя = следующий Пн–Вс (всегда следующая неделя, независимо от текущего дня).

---

## Переменные окружения

**Hub (`.env.hub`):**
```
BOT_TOKEN=
ADMIN_USERNAME=        # без @
GOLOGIN_API_TOKEN=
HUB_SECRET=            # общий секрет Hub ↔ Agent
HUB_HOST=127.0.0.1
HUB_PORT=8082
DATABASE_URL=sqlite+aiosqlite:///./hub.db
```

**Agent (`.env.agent`):**
```
HUB_URL=               # http://... адрес Hub
HUB_SECRET=
AGENT_ID=              # уникальное имя устройства
OWNER_TELEGRAM_ID=     # числовой Telegram ID владельца
AGENT_PORT=8081
AGENT_HOST=127.0.0.1
```

---

## Запуск

```bash
# Hub (сервер):
nohup /Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10 hub_main.py >> /tmp/hub.log 2>&1 &

# Agent (на Mac сотрудника):
bash setup_agent.sh   # первый раз
# или:
nohup .venv/bin/python agent_main.py >> /tmp/massmo-agent.log 2>&1 &

# Перезапуск Hub:
pkill -f "hub_main" && lsof -ti :8082 | xargs kill -9 2>/dev/null
nohup /Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10 hub_main.py >> /tmp/hub.log 2>&1 &
```

---

## Git-конвенции

```
<type>(gologin-bot): <description>
```
Типы: `feat`, `fix`, `refactor`, `chore`

Не коммитить: `.env`, `.env.agent`, `.env.hub`, `__pycache__`, `*.db`, `massmo_jwt_cache.json`.

---

## Что не трогать

- `bot/` — legacy (Phase 1), используется только `bot/services/` агентом
- `main.py` — deprecated entry point (Phase 1)
- `Token` модель и `token_*` функции в `bot/keyboards/builder.py` — legacy
