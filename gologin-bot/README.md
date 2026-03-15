# GoLogin Shift Manager Bot

Telegram-бот + локальный веб-дашборд для управления рабочими сменами на GoLogin-профилях (платформа MassMO).

## Суть

Несколько операторов одновременно работают с папками GoLogin-профилей на платформе MassMO. Бот управляет доступом: оператор берёт папку (токен), запускает ТМ и M1…M15 профили, работает, завершает смену. Веб-панель автоматически открывается в ТМ-браузере — оператор видит состояние всех M-профилей в реальном времени и управляет ими через CDP.

## Стек

- `aiogram 3.x` — Telegram-бот, callback-роутеры, single-message UI
- `FastAPI` + `uvicorn` — веб-панель оператора на `localhost:8080`
- `SQLAlchemy 2.0 async` + `aiosqlite` — хранение папок и состояния смен
- `pydantic-settings` — конфиг через `.env`
- `httpx` — GoLogin Desktop API (локальный, порт 36912) + GoLogin Cloud API
- `playwright` — CDP-подключение к браузерам (парсинг и UI-автоматизация MassMO)

## Архитектура

```
gologin-bot/
├── main.py                      # aiogram polling + uvicorn (оба в одном event loop)
├── bot/
│   ├── core/config.py           # настройки из .env
│   ├── db/                      # SQLAlchemy models + repository
│   ├── handlers/                # Telegram callback-хендлеры
│   ├── keyboards/               # inline-клавиатуры
│   ├── middlewares/             # DbSessionMiddleware
│   └── services/
│       ├── gologin.py           # GoLoginService + GoLoginCloudService
│       ├── sync.py              # синхронизация папок из GoLogin Cloud
│       ├── massmo.py            # разовый парсинг MassMO (Telegram-сообщение)
│       ├── massmo_actions.py    # Playwright UI-автоматизация massmo.io
│       ├── window_agent.py      # per-window state machine (asyncio.Task)
│       ├── orchestrator.py      # singleton: управляет WindowAgent-ами
│       └── ws_manager.py        # WebSocket broadcast hub
└── web/
    ├── app.py                   # FastAPI factory
    ├── api/routes.py            # REST: /session, /windows, /windows/{id}/command
    ├── api/ws.py                # WebSocket /ws — real-time обновления
    ├── models/schemas.py        # Pydantic: WindowState, CommandRequest, ...
    └── static/index.html        # SPA оператора (vanilla JS)
```

## Установка

```bash
# 1. Зависимости
pip install -r requirements.txt
playwright install chromium

# 2. Переменные окружения
cp .env.example .env
# Заполнить BOT_TOKEN, ADMIN_USERNAME, GOLOGIN_API_TOKEN
```

## Запуск

```bash
python3 main.py
```

Логи: stdout / `/tmp/bot.log` при nohup.
БД: `gologin.db` (SQLite, создаётся автоматически).
Веб-панель: `http://127.0.0.1:8080`

**Перезапуск (убить всё и стартовать чисто):**

```bash
ps aux | grep "python.*main" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
lsof -ti :8080 | xargs kill -9 2>/dev/null
nohup python3 main.py >> /tmp/bot.log 2>&1 &
```

## Флоу оператора

1. `/start` → главное меню
2. `[Начать смену]` → список свободных папок GoLogin
3. Выбор папки → пикер количества M-профилей
4. Запуск: бот стартует ТМ + M1…MN через GoLogin Desktop API
5. ТМ-браузер автоматически открывает веб-панель `localhost:8080`
6. Оператор управляет выплатами через дашборд (CDP → MassMO)
7. `[Завершить смену]` → профили останавливаются, папка освобождается

## Веб-панель

Доступна по `http://127.0.0.1:8080`. Открывается автоматически при запуске смены.

**Возможности на карточке каждого M-профиля:**
- Статус в реальном времени: `IDLE` / `SEARCHING` / `ACTIVE_PAYOUT` / `DISABLED` / `ERROR`
- Данные активной выплаты: сумма, банк, получатель, таймер, курс
- Кнопки: Получить выплату, Отменить, Обновить
- Выбор банка: Tinkoff, Сбер, Альфа, ВТБ
- Настройка лимитов мин/макс
- Переключатели: телефон / карта / счёт
- Загрузка PDF-чека

## Переменные окружения

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен от @BotFather |
| `ADMIN_USERNAME` | Telegram username администратора (без @) |
| `GOLOGIN_API_TOKEN` | Токен GoLogin Cloud API |
| `WEB_HOST` | Хост веб-панели (default: `127.0.0.1`) |
| `WEB_PORT` | Порт веб-панели (default: `8080`) |

## Синхронизация папок

При старте бот автоматически синхронизирует папки из GoLogin Cloud. Вручную: `/sync` (только admin).
Папки fetчатся через GoLogin Cloud API, профили — поштучно с задержкой 1s (rate limit), при 429 — exponential backoff.

## Защита от race conditions

Атомарный `UPDATE folders SET is_free=0 WHERE id=? AND is_free=1` — если `rowcount == 0`, папка уже занята другим оператором.

## Требования

- Python 3.11+
- GoLogin Desktop запущен на `localhost:36912`
- GoLogin Cloud API токен
