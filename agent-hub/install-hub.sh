#!/usr/bin/env bash
# install-hub.sh — MassMO Hub installer (Linux VPS)
#
# Usage:
#   wget -qO- https://raw.githubusercontent.com/luzenloh/universal-hub/main/agent-hub/install-hub.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/luzenloh/universal-hub/main/agent-hub/install-hub.sh | bash
#   ./install-hub.sh
#
# What it does:
#   1. Installs system dependencies (git, python3, pip)
#   2. Clones / updates the repo from GitHub
#   3. Installs Python dependencies via pip
#   4. Interactive .env.hub setup
#   5. Registers as a systemd service (auto-start on boot)
#   6. Starts the hub
#
# Requirements: bash, sudo access, Debian/Ubuntu or RHEL/CentOS
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

GITHUB_REPO="luzenloh/universal-hub"
GITHUB_SUBDIR="agent-hub"
INSTALL_DIR="${HOME}/hub"
SERVICE_NAME="massmo-hub"
LOG_FILE="/tmp/hub.log"
PYTHON_MIN="3.10"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

prompt() {
    local var="$1" label="$2" default="${3:-}"
    if [[ -n "$default" ]]; then
        read -rp "  ${label} [${default}]: " val </dev/tty
        val="${val:-$default}"
    else
        read -rp "  ${label}: " val </dev/tty
        while [[ -z "$val" ]]; do
            echo "  (обязательное поле)"
            read -rp "  ${label}: " val </dev/tty
        done
    fi
    printf -v "$var" '%s' "$val"
}

echo ""
echo -e "  ${BOLD}${CYAN}MassMO Hub Installer${NC}"
echo "  ─────────────────────────────────────────────────────"
echo ""

# ── Step 1: System dependencies ───────────────────────────────────────────────
info "Checking system packages..."

install_pkg() {
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y "$@" -q
    elif command -v yum &>/dev/null; then
        sudo yum install -y "$@" -q
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y "$@" -q
    else
        die "Unsupported package manager. Install manually: $*"
    fi
}

if ! command -v git &>/dev/null; then
    info "Installing git..."
    install_pkg git
fi
ok "git: $(git --version)"

# Find Python 3.10+
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        if "$cmd" -c "import sys; assert sys.version_info >= (3,10)" 2>/dev/null; then
            PYTHON="$cmd"
            ok "Python: $($cmd --version)"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    info "Installing Python 3.10+..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y python3 python3-pip python3-venv -q
        PYTHON="python3"
    elif command -v yum &>/dev/null; then
        sudo yum install -y python310 python310-pip -q
        PYTHON="python3.10"
    else
        die "Python 3.10+ not found. Install manually and re-run."
    fi
    ok "Python: $($PYTHON --version)"
fi

# pip
if ! "$PYTHON" -m pip --version &>/dev/null; then
    info "Installing pip..."
    install_pkg python3-pip
fi

# ── Step 2: Clone / update repo ───────────────────────────────────────────────
info "Getting Hub source from GitHub..."

if [[ -d "${INSTALL_DIR}/.git" ]]; then
    info "Repo already exists — pulling latest..."
    git -C "$INSTALL_DIR" pull --ff-only
    ok "Updated to latest"
else
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "https://github.com/${GITHUB_REPO}.git" "$INSTALL_DIR" --depth=1
    ok "Cloned to $INSTALL_DIR"
fi

# Move into the agent-hub subdirectory
WORK_DIR="${INSTALL_DIR}/${GITHUB_SUBDIR}"
[[ -d "$WORK_DIR" ]] || die "Expected subdirectory not found: $WORK_DIR"
cd "$WORK_DIR"

# ── Step 3: Create venv & install Python dependencies ─────────────────────────
info "Setting up Python virtual environment..."
if [[ ! -d "${WORK_DIR}/.venv" ]]; then
    "$PYTHON" -m venv "${WORK_DIR}/.venv"
    ok "venv created"
else
    ok "venv already exists"
fi
VENV_PY="${WORK_DIR}/.venv/bin/python"

info "Installing Python dependencies (this may take a minute)..."
"$VENV_PY" -m pip install --upgrade pip -q --no-cache-dir
"$VENV_PY" -m pip install -r requirements.txt -q --no-cache-dir
ok "Dependencies installed"

# ── Step 4: Interactive .env.hub setup ────────────────────────────────────────
SKIP_ENV=false
if [[ -f ".env.hub" ]]; then
    warn ".env.hub already exists."
    read -rp "  Перезаписать? [y/N]: " overwrite </dev/tty
    if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
        info "Оставляю существующий .env.hub"
        SKIP_ENV=true
    fi
fi

if [[ "$SKIP_ENV" == "false" ]]; then
    echo ""
    echo -e "  ${BOLD}Настройка Hub${NC}"
    echo "  ─────────────────────────────────────────────────────"
    echo "  Enter = использовать значение по умолчанию [в скобках]"
    echo ""

    # Generate a random secret if openssl available
    RANDOM_SECRET=$(openssl rand -hex 16 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(16))")

    prompt BOT_TOKEN      "Telegram Bot Token (от @BotFather)"
    prompt ADMIN_USERNAME "Твой Telegram username (без @)"
    prompt HUB_SECRET     "Секретный ключ Hub" "$RANDOM_SECRET"
    prompt GOLOGIN_TOKEN  "GoLogin API токен (Enter = пропустить)" ""
    prompt HUB_PUBLIC_URL "Публичный URL Hub (http://IP_СЕРВЕРА:8082)" "http://127.0.0.1:8082"

    cat > .env.hub <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_USERNAME=${ADMIN_USERNAME}
HUB_SECRET=${HUB_SECRET}
GOLOGIN_API_TOKEN=${GOLOGIN_TOKEN}
HUB_HOST=0.0.0.0
HUB_PORT=8082
HUB_PUBLIC_URL=${HUB_PUBLIC_URL}
DATABASE_URL=sqlite+aiosqlite:///./hub.db
EOF

    chmod 600 .env.hub
    ok ".env.hub записан"
fi

# ── Step 5: Ensure swap exists (prevents OOM deadlock) ────────────────────────
if [[ $(swapon --show 2>/dev/null | wc -l) -le 1 ]]; then
    info "No swap detected — creating 1 GB swapfile..."
    if sudo fallocate -l 1G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none; then
        sudo chmod 600 /swapfile
        sudo mkswap /swapfile -q
        sudo swapon /swapfile
        grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
        ok "Swap 1 GB создан и активирован"
    else
        warn "Не удалось создать swap — продолжаю без него"
    fi
else
    ok "Swap уже есть: $(swapon --show --noheadings 2>/dev/null | head -1)"
fi

# ── Step 6: Create start script ───────────────────────────────────────────────
cat > "${WORK_DIR}/start.sh" <<STARTSCRIPT
#!/usr/bin/env bash
cd "${WORK_DIR}"
exec ${WORK_DIR}/.venv/bin/python hub_main.py >> "${LOG_FILE}" 2>&1
STARTSCRIPT
chmod +x "${WORK_DIR}/start.sh"

# ── Step 7: Register as systemd service ───────────────────────────────────────
if command -v systemctl &>/dev/null; then
    info "Registering systemd service..."

    sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<UNIT
[Unit]
Description=MassMO Hub
After=network.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${WORK_DIR}
ExecStart=${WORK_DIR}/start.sh
Restart=on-failure
RestartSec=10
# Memory limits — prevents OOM deadlock on low-RAM servers
MemoryMax=512M
MemorySwapMax=512M
OOMScoreAdj=500

[Install]
WantedBy=multi-user.target
UNIT

    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl restart "$SERVICE_NAME"
    ok "systemd service зарегистрирован: $SERVICE_NAME"

else
    warn "systemd не найден — запускаю через nohup"
    pkill -f hub_main 2>/dev/null || true
    sleep 1
    nohup "${WORK_DIR}/start.sh" &
    ok "Hub запущен в фоне"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
sleep 2
echo ""
echo -e "  ${GREEN}${BOLD}✅ MassMO Hub запущен!${NC}"
echo "  ─────────────────────────────────────────────────────"
echo "  Install dir : $WORK_DIR"
echo "  Hub API     : http://0.0.0.0:8082"
echo "  Логи        : $LOG_FILE"
echo ""
echo "  Полезные команды:"

if command -v systemctl &>/dev/null; then
    echo "    sudo systemctl status $SERVICE_NAME   # статус"
    echo "    sudo systemctl restart $SERVICE_NAME  # перезапуск"
    echo "    sudo systemctl stop $SERVICE_NAME     # остановить"
    echo "    journalctl -u $SERVICE_NAME -f        # логи"
else
    echo "    tail -f $LOG_FILE                     # логи"
    echo "    pkill -f hub_main                     # остановить"
fi

echo ""
echo "  Следующие шаги:"
echo "    1. Открой порт 8082 в файрволе сервера"
echo "    2. Зарегистрируй агентов: /register_agent <username> в Telegram"
echo ""
