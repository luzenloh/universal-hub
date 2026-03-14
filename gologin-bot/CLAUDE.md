# CLAUDE.md — gologin-bot

Telegram-бот для управления рабочими сменами на GoLogin-профилях.

---

## Суть проекта

Несколько пользователей одновременно работают с GoLogin-профилями на платформе MassMO.
Бот управляет доступом: пользователь берёт токен (папку GoLogin), запускает профили, работает, завершает смену.

---

## Стек

- `aiogram 3.x` — Telegram-бот, callback-роутеры, edit_text (single-message UI)
- `SQLAlchemy 2.0 async` + `aiosqlite` — ORM, атомарные UPDATE
- `pydantic-settings` — конфиг через `.env`
- `httpx` — GoLogin Desktop API + GoLogin Cloud API
- `playwright` — подключение к запущенным браузерам через CDP для парсинга MassMO

---

## Архитектура

```
bot/
├── core/config.py          # настройки из .env
├── db/
│   ├── models.py           # Folder (основная), Token (legacy)
│   ├── repository.py       # FolderRepository — все операции с БД
│   └── base.py             # async engine + session factory
├── handlers/
│   ├── common.py           # /start
│   ├── shift.py            # вся логика смены (callback-хендлеры)
│   └── admin.py            # /folders, /sync
├── keyboards/builder.py    # фабрики inline-клавиатур
├── middlewares/db.py       # DbSessionMiddleware
└── services/
    ├── gologin.py          # GoLoginService (Desktop API) + GoLoginCloudService
    ├── sync.py             # sync_folders() — синхронизация папок из облака
    └── massmo.py           # парсинг страниц MassMO через Playwright CDP
```

---

## Ключевые концепции

### Single-message UI
Вся навигация через `edit_text` на одном сообщении. Новые сообщения не отправляются кроме:
- результатов парсинга MassMO после запуска профилей

### Атомарное занятие токена
```python
UPDATE folders SET is_free=0, assigned_to=?, ... WHERE id=? AND is_free=1
```
Никакого SELECT-before-UPDATE. Если `rowcount == 0` — токен уже занят.

### Модель Folder
- `gologin_id` — ID папки в GoLogin Cloud
- `main_profile_id` — профиль "ТМ" (определяется по имени: `"тм"` или `"глав" in name`)
- `numbered_profile_ids` — JSON список ID профилей M1…M15, отсортированных численно
- `selected_count` — сколько M-профилей выбрал пользователь при запуске

### Идентификация профилей при синхронизации
- Главный (ТМ): `name.lower().strip() == "тм"` или `"глав" in name.lower()`
- Числовая сортировка M1…M15: `re.search(r"\d+", name)` → `int` (иначе строки дают M10 < M2)

### GoLogin APIs
- **Desktop** (`localhost:36912`): запуск/стоп профилей, возвращает `wsUrl`
- **Cloud** (`api.gologin.com`): получение папок и названий профилей
  - Пагинация сломана — всегда возвращает первые 30 профилей
  - Профили fetчатся поштучно через `GET /browser/{id}` с задержкой 1с (rate limit)
  - При 429 — exponential backoff: 10s × 2^attempt

### Парсинг MassMO
Сервис `massmo.py`:
1. Подключается к браузеру через `playwright.chromium.connect_over_cdp(ws_url)`
2. Находит вкладку massmo.io или открывает её
3. Парсит `page.inner_text("body")` регулярками
4. `browser.close()` не вызывать — это убивает GoLogin-профиль
5. Запускается через `asyncio.create_task()` в фоне, отправляет отдельным сообщением

---

## Callback-схема

```
shift:folders               — список токенов
shift:folder:{id}           — выбор свободного токена → пикер количества
shift:folder_info:{id}      — инфо о занятом токене
shift:count:{folder_id}:{n} — навигация пикера
shift:noop                  — кнопка-заглушка (отображение числа)
shift:launch_folder:{id}:{count} — запуск: ТМ + M1…MN + парсинг MassMO
shift:force_folder:{id}     — принудительное освобождение (только admin)
shift:release               — завершить смену
```

---

## Переменные окружения (.env)

```
BOT_TOKEN=
ADMIN_USERNAME=            # без @
GOLOGIN_API_TOKEN=         # токен GoLogin Cloud API
```

---

## Запуск

```bash
python3 main.py
```

Логи: `/tmp/bot.log` (если запущен через nohup).
БД: `gologin.db` (SQLite, создаётся автоматически).
Синхронизация папок — автоматически при старте, вручную через `/sync`.

---

## Git-конвенции

### Когда коммитить
- **Только после проверки работоспособности** — запустил бота, потыкал фичу в Telegram, убедился что работает
- Один коммит = одна логическая фича/фикс
- Каждый коммит оставляет бота в рабочем состоянии

### Формат (Conventional Commits)
```
<type>(gologin-bot): <description>
```

**Типы:**
- `feat` — новая функция
- `fix` — исправление бага
- `refactor` — рефакторинг без изменения поведения
- `chore` — зависимости, конфиги

**Примеры:**
```
feat(gologin-bot): add MassMO scraping via Playwright CDP on profile launch
fix(gologin-bot): fix M1/M2 sort bug — use numeric sort instead of string
feat(gologin-bot): add count picker with separate launch button
```

### Перед коммитом
- Нет `.env` в staging
- Нет `__pycache__`, `*.db`, `bot.log`

---

## Что не трогать

- `Token` модель и связанные `token_*` функции в `builder.py` — legacy, оставлены для совместимости
- `seed.py` — старый скрипт заполнения токенов, не используется
