# agent-hub — инструкции для Claude

## Деплой: ВСЕГДА коммитить и пушить после изменений

После любых изменений в коде — **обязательно коммитить и пушить в main**.
Агенты на серверах обновляются через `install-agent.sh`, который тянет файлы
из GitHub (`raw.githubusercontent.com`). Без пуша изменения до агентов не доходят.

```bash
git add -A
git commit -m "описание изменений"
git push origin main
```

После пуша пользователь запускает на сервере:
```bash
curl -fsSL https://raw.githubusercontent.com/luzenloh/universal-hub/main/agent-hub/install-agent.sh \
  | bash -s -- GLAGENT_<token>
```

## Перезапуск хаба на сервере

`lsof` нет на сервере. Простой `kill` не убивает процесс — нужен **`kill -9`**.

```bash
cd ~/hub/agent-hub
git pull origin main
kill -9 $(fuser 8082/tcp)
nohup .venv/bin/python hub_main.py >> /tmp/hub.log 2>&1 &
tail -f /tmp/hub.log
```

## Архитектура

- **Hub** (`hub_main.py`) — центральный сервер, Telegram-бот, база агентов
- **Agent** (`agent_main.py`) — запускается на машине оператора, управляет браузерами
- **Dashboard** (`web/static/index.html`) — SPA на ванильном JS, API через `/api/v1/`
- **Tunnel** — cloudflared, автоматически пробрасывает агент в интернет

## Токены агентов

Формат: `GLAGENT_<base64({"hub_url":"...","jti":"..."})>`

Токены **многоразовые** (действуют 7 дней от создания).
Один и тот же токен можно использовать для переустановки/перезапуска агента
с последним кодом из GitHub.

Генерируются командой `/register_agent <username>` в Telegram-боте хаба.

## PayFast

- Credentials хранятся в `massmo_secrets["payfast"]` = `{"email": "...", "password": "..."}`
- Устанавливаются хабом через `/agent/start_shift` → `orchestrator.set_shift_secrets()`
- API: `GET /api/v1/payfast/orders` — список BT-ордеров (type=checks)
- При рестарте агента без старта смены — `_shift_secrets` не восстанавливаются,
  PayFast вкладка покажет "не настроен"
