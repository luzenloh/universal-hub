#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  MassMO Agent — установка и запуск
#  Запусти один раз: bash setup_agent.sh
# ─────────────────────────────────────────────────────────────

set -e
# Always run from the directory where this script lives
cd "$(dirname "$0")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${CYAN}→${NC} $1"; }

echo ""
echo "  MassMO Agent Setup"
echo "───────────────────────────────────"

# ── 1. Python — найти или установить ≥ 3.10 ──────────────────
MIN_MINOR=10   # минимальная minor-версия Python 3.x

find_python() {
  # Перебираем кандидатов от новых к старым
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
      local minor
      minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
      local major
      major=$("$candidate" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
      if [ "$major" = "3" ] && [ "$minor" -ge "$MIN_MINOR" ] 2>/dev/null; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON=$(find_python || true)

if [ -z "$PYTHON" ]; then
  warn "Python 3.${MIN_MINOR}+ не найден. Текущий: $(python3 --version 2>/dev/null || echo 'не установлен')"

  if command -v brew &>/dev/null; then
    info "Устанавливаю Python 3.11 через Homebrew..."
    brew install python@3.11
    # Homebrew кладёт python3.11 в PATH после установки
    BREW_PREFIX=$(brew --prefix)
    export PATH="${BREW_PREFIX}/bin:${BREW_PREFIX}/opt/python@3.11/bin:$PATH"
    PYTHON=$(find_python || true)
    if [ -z "$PYTHON" ]; then
      err "Homebrew установил Python, но он не найден в PATH. Перезапусти терминал и повтори."
    fi
    ok "Python установлен: $($PYTHON --version)"
  elif command -v pyenv &>/dev/null; then
    info "Устанавливаю Python 3.11 через pyenv..."
    pyenv install -s 3.11.9
    pyenv local 3.11.9
    PYTHON=$(pyenv which python3)
    ok "Python установлен через pyenv: $($PYTHON --version)"
  else
    echo ""
    echo "  Homebrew или pyenv не найдены."
    echo "  Установи Python вручную:"
    echo "    • Homebrew:  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    echo "    •            brew install python@3.11"
    echo "    • pyenv:     brew install pyenv && pyenv install 3.11.9 && pyenv local 3.11.9"
    echo "    • Напрямую:  https://www.python.org/downloads/"
    err "Установи Python 3.${MIN_MINOR}+ и запусти скрипт снова."
  fi
else
  ok "Python: $($PYTHON --version)"
fi

# ── 2. Виртуальное окружение (.venv) ─────────────────────────
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
  info "Создаю виртуальное окружение в .venv/ ..."
  "$PYTHON" -m venv "$VENV_DIR"
  ok "Виртуальное окружение создано"
else
  # Проверить что venv собран нужной версией Python
  VENV_MINOR=$("$VENV_DIR/bin/python" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0")
  if [ "$VENV_MINOR" -lt "$MIN_MINOR" ] 2>/dev/null; then
    warn "Старое .venv (Python 3.${VENV_MINOR}), пересоздаю..."
    rm -rf "$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Виртуальное окружение пересоздано"
  else
    ok "Виртуальное окружение уже есть (.venv, Python 3.${VENV_MINOR})"
  fi
fi

PIP="$VENV_DIR/bin/pip"
PYTHON_VENV="$VENV_DIR/bin/python"

# ── 3. Зависимости (из requirements-agent.txt или встроенный список) ───
echo ""
info "Устанавливаю зависимости..."

# Сначала обновим pip чтобы не было предупреждений о старой версии
"$PIP" install -q --upgrade pip

if [ -f "requirements-agent.txt" ]; then
  "$PIP" install -q -r requirements-agent.txt
else
  # Встроенный список с версиями (на случай если файл не рядом)
  "$PIP" install -q \
    "fastapi>=0.100.0,<1.0.0" \
    "uvicorn[standard]>=0.20.0,<1.0.0" \
    "python-multipart>=0.0.9" \
    "httpx>=0.24.0,<1.0.0" \
    "pydantic>=2.0.0,<3.0.0" \
    "pydantic-settings>=2.0.0,<3.0.0" \
    "sqlalchemy>=2.0.0,<3.0.0" \
    "aiosqlite>=0.19.0" \
    "anyio>=3.6.0,<5.0.0" \
    "playwright>=1.40.0"
fi

ok "Зависимости установлены"

# ── 4. Playwright Chromium ────────────────────────────────────
info "Устанавливаю Playwright Chromium (нужен для извлечения JWT)..."
"$PYTHON_VENV" -m playwright install chromium 2>/dev/null \
  && ok "Playwright Chromium установлен" \
  || warn "Playwright Chromium не установился — попробуй вручную: .venv/bin/python -m playwright install chromium"

# ── 5. .env.agent ─────────────────────────────────────────────
if [ ! -f .env.agent ]; then
  echo ""
  echo "Настройка подключения к Hub:"
  read -p "  HUB_SECRET (спроси у администратора): " HUB_SECRET
  read -p "  AGENT_ID (например agent-mac-2): " AGENT_ID
  AGENT_ID=${AGENT_ID:-agent-mac-2}
  echo ""
  echo "  Твой Telegram ID нужен чтобы смена запускалась только на твоём Mac."
  echo "  Узнать свой ID: напиши @userinfobot в Telegram."
  read -p "  OWNER_TELEGRAM_ID (твой числовой Telegram ID): " OWNER_TELEGRAM_ID
  read -p "  HUB_URL (спроси у администратора, например http://1.2.3.4:8082): " HUB_URL

  cat > .env.agent << EOF
HUB_URL=${HUB_URL}
HUB_SECRET=${HUB_SECRET}
AGENT_ID=${AGENT_ID}
OWNER_TELEGRAM_ID=${OWNER_TELEGRAM_ID}
AGENT_PORT=8081
AGENT_HOST=127.0.0.1
WEB_HOST=127.0.0.1
WEB_PORT=8081
EOF
  ok ".env.agent создан"
else
  ok ".env.agent уже существует, пропускаю"
fi

# ── 6. cloudflared (опционально, но важно) ───────────────────
if ! command -v cloudflared &>/dev/null; then
  warn "cloudflared не найден — Hub не сможет управлять агентом удалённо"
  if command -v brew &>/dev/null; then
    read -p "Установить cloudflared через Homebrew? (y/n): " INSTALL_CF
    if [ "$INSTALL_CF" = "y" ]; then
      brew install cloudflared && ok "cloudflared установлен"
    else
      warn "Без cloudflared Hub будет работать только в локальной сети"
    fi
  else
    warn "Установи вручную: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  fi
else
  ok "cloudflared: $(cloudflared --version 2>&1 | head -1)"
fi

# ── 7. Запуск ─────────────────────────────────────────────────
echo ""
echo "───────────────────────────────────"
ok "Всё готово! Запускаю агент..."
echo ""

pkill -f "python.*agent_main" 2>/dev/null || true
sleep 1

nohup "$PYTHON_VENV" agent_main.py >> /tmp/massmo-agent.log 2>&1 &
AGENT_PID=$!
echo "  PID: $AGENT_PID"
echo "  Логи: tail -f /tmp/massmo-agent.log"
echo ""

sleep 5

if kill -0 $AGENT_PID 2>/dev/null; then
  ok "Агент запущен!"
  echo ""
  echo "  Дашборд: http://localhost:8081"
  echo ""
  echo "  Для перезапуска:"
  echo "  pkill -f agent_main && .venv/bin/python agent_main.py"
else
  echo ""
  echo "  Последние строки лога:"
  tail -20 /tmp/massmo-agent.log 2>/dev/null || true
  echo ""
  err "Агент упал. Полный лог: tail -50 /tmp/massmo-agent.log"
fi
