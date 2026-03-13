# GoLogin Token Manager Bot

Telegram-бот для управления доступом к профилям GoLogin. Несколько пользователей одновременно занимают/освобождают токены с защитой от race conditions.

## Стек

- `aiogram 3.x` — async Telegram Bot framework
- `SQLAlchemy 2.0 async` + `aiosqlite` — ORM + SQLite
- `pydantic-settings` — конфиг через `.env`

## Запуск

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Создать .env
cp .env.example .env
# Вставить BOT_TOKEN от @BotFather

# 3. Засеять тестовые токены
python seed.py

# 4. Запустить бота
python main.py
```

## Флоу пользователя

1. `/start` → показывает активный токен или кнопку `[Начать смену]`
2. `[Начать смену]` → список свободных профилей GoLogin
3. Выбор профиля → атомарное занятие токена (защита от race condition)
4. `[Освободить токен]` → токен возвращается в пул

## Защита от race conditions

Атомарный `UPDATE ... WHERE id=? AND is_free=1` — если `rowcount == 0`, токен уже занят другим пользователем. Никаких SELECT-before-UPDATE.

## Структура БД

```sql
tokens:
  id          INTEGER PRIMARY KEY AUTOINCREMENT
  name        TEXT NOT NULL          -- "Profile 1" (отображается пользователю)
  value       TEXT NOT NULL          -- реальный GoLogin токен
  is_free     BOOLEAN DEFAULT TRUE
  assigned_to INTEGER NULL           -- telegram user_id
  assigned_at DATETIME NULL
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
```
