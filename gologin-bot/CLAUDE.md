# CLAUDE.md — gologin-bot

Telegram-бот + локальный веб-дашборд для управления рабочими сменами на GoLogin-профилях (платформа MassMO).

---

## Суть проекта

Несколько пользователей одновременно работают с GoLogin-профилями на платформе MassMO.
Бот управляет доступом: пользователь берёт токен (папку GoLogin), запускает профили, работает, завершает смену.
Веб-панель (`localhost:8080`) открывается автоматически в ТМ-браузере при запуске смены — оператор видит состояние всех M-профилей в реальном времени и управляет ими через CDP.

---

## Стек

- `aiogram 3.x` — Telegram-бот, callback-роутеры, edit_text (single-message UI)
- `FastAPI` + `uvicorn` — локальный веб-дашборд на порту 8080
- `SQLAlchemy 2.0 async` + `aiosqlite` — ORM, атомарные UPDATE
- `pydantic-settings` — конфиг через `.env`
- `httpx` — GoLogin Desktop API + GoLogin Cloud API
- `playwright` — CDP-подключение к запущенным браузерам (парсинг + UI-автоматизация MassMO)

---

## Архитектура

```
gologin-bot/
├── main.py                      # aiogram (asyncio.create_task) + uvicorn (await server.serve())
├── bot/
│   ├── core/config.py           # настройки из .env (BOT_TOKEN, WEB_HOST, WEB_PORT, ...)
│   ├── db/
│   │   ├── models.py            # Folder (основная), Token (legacy)
│   │   ├── repository.py        # FolderRepository
│   │   └── base.py              # async engine + session factory
│   ├── handlers/
│   │   ├── common.py            # /start
│   │   ├── shift.py             # логика смены; после запуска профилей вызывает attach_profiles()
│   │   └── admin.py             # /folders, /sync
│   ├── keyboards/builder.py     # фабрики inline-клавиатур
│   ├── middlewares/db.py        # DbSessionMiddleware
│   └── services/
│       ├── gologin.py           # GoLoginService (Desktop API) + GoLoginCloudService
│       ├── sync.py              # sync_folders()
│       ├── massmo.py            # разовый парсинг MassMO через CDP (Telegram-сообщение)
│       ├── massmo_actions.py    # Playwright UI-автоматизация massmo.io (detect_state, click, upload, limits)
│       ├── window_agent.py      # per-window asyncio.Task + state machine (CONNECTING→IDLE→SEARCHING→ACTIVE_PAYOUT)
│       ├── orchestrator.py      # singleton: управляет WindowAgent-ами, маршрутизирует команды
│       └── ws_manager.py        # WebSocket broadcast hub
└── web/
    ├── app.py                   # FastAPI factory + lifespan
    ├── api/
    │   ├── routes.py            # REST: /session/start|stop, /windows, /windows/{id}/command|upload
    │   └── ws.py                # WebSocket /ws — real-time state updates
    ├── models/schemas.py        # Pydantic: WindowState, WindowStatus, CommandRequest, ...
    └── static/index.html        # SPA оператора (vanilla JS, без сборки)
```

---

## Ключевые концепции

### Запуск бота

```bash
python3 main.py
```

Логи: `/tmp/bot.log` (nohup). БД: `gologin.db`. Порт: `8080`.
Синхронизация папок — автоматически при старте, вручную `/sync`.

### Запуск + перезапуск

```bash
# Убить всё и запустить чисто:
ps aux | grep "python.*main" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
lsof -ti :8080 | xargs kill -9 2>/dev/null
ps aux | grep "tail -f" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
nohup python3 main.py >> /tmp/bot.log 2>&1 &
```

### Single-message UI (Telegram)
Вся навигация через `edit_text` на одном сообщении. Результаты парсинга — отдельным сообщением.

### Атомарное занятие токена
```python
UPDATE folders SET is_free=0, assigned_to=?, ... WHERE id=? AND is_free=1
```
Если `rowcount == 0` — токен уже занят.

### Модель Folder
- `gologin_id` — UUID папки в GoLogin Cloud
- `main_profile_id` — профиль "ТМ" (`"тм"` или `"глав" in name`)
- `numbered_profile_ids` — JSON список ID профилей M1…M15 (числовая сортировка)
- `selected_count` — сколько M-профилей выбрал пользователь

### GoLogin APIs
- **Desktop** (`localhost:36912`): запуск/стоп профилей, возвращает `wsUrl`
  - Если профиль уже запущен — возвращает `wsUrl: ""`. Решение: stop → sleep 5s → restart (до 2 попыток)
- **Cloud** (`api.gologin.com`): папки и названия профилей
  - Профили fetчатся поштучно с задержкой 1s (rate limit). При 429 — exponential backoff

### CDP-подключение (Playwright)
```python
browser = await pw.chromium.connect_over_cdp(ws_url)
# НИКОГДА не вызывать browser.close() — убьёт GoLogin-профиль
# pw.stop() — безопасно (отключает playwright, не трогает браузер)
```

### WindowAgent — state machine
Каждый M-профиль = отдельный `asyncio.Task`. Состояния:
- `CONNECTING` → `IDLE` ↔ `SEARCHING` → `ACTIVE_PAYOUT`
- `DISABLED` — MassMO отключил выплатчик ("Payouter: is disabled")
- `ERROR` — ошибка CDP, exponential backoff reconnect (2→4→8→...→60s)
- До первого успешного подключения — фиксированный retry каждые 3s (браузер ещё поднимается)

### detect_state — порядок проверок (важно!)
```python
# 1. ACTIVE_PAYOUT — приоритет
# 2. DISABLED — "is disabled" / "payouter"
# 3. SEARCHING — "отменить поиск" / "идет поиск" / "поиск выплаты"  ← ПЕРЕД idle!
# 4. IDLE — "нет активной заявки" / "получить выплату"
# 5. fallback → SEARCHING
```
SEARCHING проверяется до IDLE потому что "Получить выплату" может присутствовать в навигации при активном поиске.

### Автооткрытие дашборда
При запуске смены через Telegram-бот: ТМ-браузер автоматически открывает `http://127.0.0.1:8080` новой вкладкой (через `open_url_in_browser` в `massmo_actions.py`).

### Лимиты на дашборде
- Отображение: regex по тексту страницы ("МИН. сумма выплаты X RUB")
- Редактирование: `input[name='min']` / `input[name='max']` — click + Ctrl+A + fill + Tab
- Timeout для click: **5s** (не 30s по умолчанию), чтобы быстро падать если элемент не найден

---

## Callback-схема (Telegram)

```
shift:folders                    — список токенов
shift:folder:{id}                — выбор свободного токена → пикер количества
shift:folder_info:{id}           — инфо о занятом токене
shift:count:{folder_id}:{n}      — навигация пикера
shift:noop                       — кнопка-заглушка
shift:launch_folder:{id}:{count} — запуск: ТМ + M1…MN → attach_profiles() → открыть дашборд в ТМ
shift:force_folder:{id}          — принудительное освобождение (только admin)
shift:release                    — завершить смену
```

---

## Переменные окружения (.env)

```
BOT_TOKEN=
ADMIN_USERNAME=       # без @
GOLOGIN_API_TOKEN=    # токен GoLogin Cloud API
WEB_HOST=127.0.0.1
WEB_PORT=8080
```

---

## Git-конвенции

```
<type>(gologin-bot): <description>
```

Типы: `feat`, `fix`, `refactor`, `chore`

**Коммитить только после проверки работоспособности.**
Перед коммитом: нет `.env`, `__pycache__`, `*.db`, `bot.log` в staging.

---

## Что не трогать

- `Token` модель и `token_*` функции в `builder.py` — legacy
- `seed.py` — старый скрипт, не используется
