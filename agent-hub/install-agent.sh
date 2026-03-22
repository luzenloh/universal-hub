#!/usr/bin/env bash
# install-agent.sh — MassMO Agent installer (Linux / macOS)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install-agent.sh | bash -s -- GLAGENT_...
#   ./install-agent.sh GLAGENT_...
#
# What it does:
#   1. Checks / installs Python 3.10+
#   2. Checks / installs uv (fast Python package manager)
#   3. Downloads the latest agent release from GitHub
#   4. Decodes the setup token → calls /hub/claim to get .env.agent config
#   5. Writes .env.agent
#   6. Registers as a background service (systemd on Linux, launchd on macOS)
#   7. Starts the agent
#
# Requirements: bash, curl, python3 (3.10+)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
GITHUB_REPO="luzenloh/universal-hub"
GITHUB_SUBDIR="agent-hub"
INSTALL_DIR="${HOME}/.gologin-agent"
SERVICE_NAME="gologin-agent"
LOG_FILE="/tmp/gologin-agent.log"
AGENT_PORT=8081

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Args ──────────────────────────────────────────────────────────────────────
SETUP_TOKEN="${1:-}"
if [[ -z "$SETUP_TOKEN" ]]; then
    echo ""
    echo "  MassMO Agent Installer"
    echo "  ─────────────────────────────────────────────────────"
    echo "  Usage: $0 GLAGENT_<token>"
    echo ""
    echo "  Get the token from the admin via the Telegram bot:"
    echo "    /register_agent <your_username>"
    echo ""
    exit 1
fi

if [[ ! "$SETUP_TOKEN" == GLAGENT_* ]]; then
    die "Invalid token format. Expected GLAGENT_..."
fi

echo ""
echo -e "  ${CYAN}MassMO Agent Installer${NC}"
echo "  ─────────────────────────────────────────────────────"
echo ""

# ── Step 1: Python 3.10+ ──────────────────────────────────────────────────────
info "Checking Python 3.10+..."

PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(sys.version_info[:2])" 2>/dev/null || echo "(0, 0)")
        if "$cmd" -c "import sys; assert sys.version_info >= (3,10)" 2>/dev/null; then
            PYTHON="$cmd"
            ok "Found $cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    warn "Python 3.10+ not found."
    OS="$(uname -s)"
    if [[ "$OS" == "Darwin" ]]; then
        info "Install via Homebrew: brew install python@3.12"
        info "Or via pyenv:        pyenv install 3.12 && pyenv global 3.12"
    else
        info "Install via your package manager, e.g.:"
        info "  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
        info "  Fedora/RHEL:   sudo dnf install python3.12"
        info "  Or via pyenv:  https://github.com/pyenv/pyenv"
    fi
    die "Please install Python 3.10+ and re-run the installer."
fi

# ── Step 2: uv ────────────────────────────────────────────────────────────────
info "Checking uv..."

if ! command -v uv &>/dev/null; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for this session
    export PATH="${HOME}/.cargo/bin:${HOME}/.local/bin:$PATH"
fi

if ! command -v uv &>/dev/null; then
    # Try common locations
    for p in "${HOME}/.cargo/bin/uv" "${HOME}/.local/bin/uv"; do
        if [[ -x "$p" ]]; then UV="$p"; break; fi
    done
    UV="${UV:-uv}"
else
    UV="uv"
fi

ok "uv: $($UV --version 2>/dev/null || echo 'found')"

# ── Step 2b: cloudflared ──────────────────────────────────────────────────────
info "Checking cloudflared..."

if ! command -v cloudflared &>/dev/null; then
    info "Installing cloudflared..."
    OS="$(uname -s)"
    ARCH="$(uname -m)"
    if [[ "$OS" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            brew install cloudflare/cloudflare/cloudflared -q \
                && ok "cloudflared installed via Homebrew" \
                || warn "Homebrew install failed, trying direct download..."
        fi
        if ! command -v cloudflared &>/dev/null; then
            CF_ARCH="amd64"
            [[ "$ARCH" == "arm64" ]] && CF_ARCH="arm64"
            curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-${CF_ARCH}" \
                -o /usr/local/bin/cloudflared \
                && chmod +x /usr/local/bin/cloudflared \
                && ok "cloudflared installed to /usr/local/bin" \
                || warn "Could not install cloudflared — tunnel will not be available"
        fi
    elif [[ "$OS" == "Linux" ]]; then
        CF_ARCH="amd64"
        [[ "$ARCH" == "aarch64" ]] && CF_ARCH="arm64"
        curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" \
            -o /usr/local/bin/cloudflared \
            && chmod +x /usr/local/bin/cloudflared \
            && ok "cloudflared installed to /usr/local/bin" \
            || warn "Could not install cloudflared — tunnel will not be available"
    else
        warn "Unsupported OS for cloudflared auto-install. Install manually: https://developers.cloudflare.com/cloudflared/install"
    fi
else
    ok "cloudflared: $(cloudflared --version 2>/dev/null | head -1)"
fi

# ── Step 3: Download agent ────────────────────────────────────────────────────
info "Downloading latest agent release..."
mkdir -p "$INSTALL_DIR"

RELEASE_URL="https://github.com/${GITHUB_REPO}/releases/latest/download/agent.tar.gz"
RAW_BASE="https://raw.githubusercontent.com/${GITHUB_REPO}/main/${GITHUB_SUBDIR}"

if curl -fsSL "$RELEASE_URL" -o /tmp/agent.tar.gz 2>/dev/null; then
    tar xzf /tmp/agent.tar.gz -C "$INSTALL_DIR" --strip-components=1
    rm -f /tmp/agent.tar.gz
    ok "Agent downloaded to $INSTALL_DIR"
else
    # Fallback 1: if script is run from inside the repo directory
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    if [[ -f "$SCRIPT_DIR/agent_main.py" ]]; then
        warn "GitHub release not found. Copying from current directory..."
        rsync -a --exclude='.env*' --exclude='.venv' --exclude='__pycache__' \
              --exclude='*.db' --exclude='hub.db' --exclude='hub_main.py' \
              "$SCRIPT_DIR/" "$INSTALL_DIR/"
        ok "Agent copied from $SCRIPT_DIR"
    else
        # Fallback 2: download individual files from raw.githubusercontent.com
        warn "GitHub release not found. Downloading files from raw GitHub..."
        AGENT_FILES=(
            "agent_main.py"
            "requirements-agent.txt"
            "VERSION"
            "agent/__init__.py"
            "agent/core/__init__.py"
            "agent/core/config.py"
            "agent/services/__init__.py"
            "agent/services/hub_client.py"
            "agent/services/tunnel.py"
            "bot/__init__.py"
            "bot/services/__init__.py"
            "bot/services/orchestrator.py"
            "bot/services/ws_manager.py"
            "bot/services/inbound_controller.py"
            "bot/services/window_agent.py"
            "bot/services/massmo_api.py"
            "bot/services/massmo_actions.py"
            "bot/services/payfast_client.py"
            "bot/services/montera_client.py"
            "bot/services/browser.py"
            "bot/services/gologin.py"
            "web/__init__.py"
            "web/app.py"
            "web/api/__init__.py"
            "web/api/routes.py"
            "web/api/ws.py"
            "web/api/agent_routes.py"
            "web/models/__init__.py"
            "web/models/schemas.py"
            "web/static/index.html"
        )
        for f in "${AGENT_FILES[@]}"; do
            mkdir -p "$INSTALL_DIR/$(dirname "$f")"
            curl -fsSL "${RAW_BASE}/${f}" -o "${INSTALL_DIR}/${f}" \
                || die "Failed to download ${f} from ${RAW_BASE}/${f}"
        done
        ok "Agent downloaded from raw GitHub to $INSTALL_DIR"
    fi
fi

# ── Step 4: Decode token + claim config ───────────────────────────────────────
info "Decoding setup token..."

# Token format: GLAGENT_<base64url({"hub_url":"...","jti":"..."})>
B64="${SETUP_TOKEN#GLAGENT_}"
# Restore base64 padding
PADDED="$B64"
case $(( ${#B64} % 4 )) in
    2) PADDED="${B64}==" ;;
    3) PADDED="${B64}=" ;;
esac

# Decode JSON payload
PAYLOAD=$($PYTHON -c "
import base64, json, sys
b = '$PADDED'
# urlsafe base64
b = b.replace('-','+').replace('_','/')
data = base64.b64decode(b).decode()
p = json.loads(data)
print(p['hub_url'])
print(p['jti'])
" 2>/dev/null) || die "Failed to decode setup token. Make sure it starts with GLAGENT_"

HUB_URL=$(echo "$PAYLOAD" | head -1)
JTI=$(echo "$PAYLOAD" | tail -1)

info "Hub URL: $HUB_URL"
info "Claiming config from Hub..."

CLAIM_RESPONSE=$(curl -fsSL "${HUB_URL}/hub/claim/${JTI}" 2>/dev/null) \
    || die "Failed to claim config from Hub.\n  Make sure the Hub is running and accessible at: $HUB_URL\n  Token is valid for 7 days from creation."

# Parse claim response
HUB_SECRET=$($PYTHON -c "import json,sys; d=json.loads('''$CLAIM_RESPONSE'''); print(d['hub_secret'])")
AGENT_ID=$($PYTHON -c "import json,sys; d=json.loads('''$CLAIM_RESPONSE'''); print(d['agent_id'])")
OWNER_TG_ID=$($PYTHON -c "import json,sys; d=json.loads('''$CLAIM_RESPONSE'''); print(d['owner_telegram_id'])")
AGENT_PORT=$($PYTHON -c "import json,sys; d=json.loads('''$CLAIM_RESPONSE'''); print(d.get('agent_port', 8081))")

ok "Config received: agent_id=$AGENT_ID"

# ── Step 5: Write .env.agent ──────────────────────────────────────────────────
info "Writing .env.agent..."

cat > "${INSTALL_DIR}/.env.agent" <<EOF
HUB_URL=${HUB_URL}
HUB_SECRET=${HUB_SECRET}
AGENT_ID=${AGENT_ID}
OWNER_TELEGRAM_ID=${OWNER_TG_ID}
AGENT_PORT=${AGENT_PORT}
AGENT_HOST=127.0.0.1
EOF

chmod 600 "${INSTALL_DIR}/.env.agent"
ok ".env.agent written (permissions: 600)"

# ── Step 6: Install dependencies ──────────────────────────────────────────────
info "Installing Python dependencies..."
cd "$INSTALL_DIR"
$UV pip install -r requirements-agent.txt --python "$PYTHON" --system -q
ok "Dependencies installed"

# ── Step 7: Register as startup service ───────────────────────────────────────
OS="$(uname -s)"

write_start_script() {
    cat > "${INSTALL_DIR}/start.sh" <<STARTSCRIPT
#!/usr/bin/env bash
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.cargo/bin:${HOME}/.local/bin:\$PATH"
cd "${INSTALL_DIR}"
exec $UV run --python "$PYTHON" agent_main.py >> "$LOG_FILE" 2>&1
STARTSCRIPT
    chmod +x "${INSTALL_DIR}/start.sh"
}

if [[ "$OS" == "Linux" ]]; then
    info "Registering systemd user service..."
    write_start_script

    mkdir -p "${HOME}/.config/systemd/user"
    cat > "${HOME}/.config/systemd/user/${SERVICE_NAME}.service" <<UNIT
[Unit]
Description=MassMO GoLogin Agent
After=network.target

[Service]
Type=simple
ExecStart=${INSTALL_DIR}/start.sh
Restart=on-failure
RestartSec=10
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

[Install]
WantedBy=default.target
UNIT

    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    systemctl --user restart "$SERVICE_NAME"
    ok "systemd user service registered: $SERVICE_NAME"
    info "Logs: journalctl --user -u $SERVICE_NAME -f"
    info "Stop:  systemctl --user stop $SERVICE_NAME"
    info "Start: systemctl --user start $SERVICE_NAME"

elif [[ "$OS" == "Darwin" ]]; then
    info "Registering launchd user agent..."
    write_start_script

    PLIST_DIR="${HOME}/Library/LaunchAgents"
    PLIST_FILE="${PLIST_DIR}/com.massmo.${SERVICE_NAME}.plist"
    mkdir -p "$PLIST_DIR"

    cat > "$PLIST_FILE" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.massmo.${SERVICE_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${INSTALL_DIR}/start.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
</dict>
</plist>
PLIST

    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    launchctl load -w "$PLIST_FILE"
    ok "launchd agent registered: com.massmo.${SERVICE_NAME}"
    info "Logs: tail -f $LOG_FILE"
    info "Stop:  launchctl unload $PLIST_FILE"
    info "Start: launchctl load -w $PLIST_FILE"

else
    warn "Unsupported OS: $OS. Creating start.sh only."
    write_start_script
    info "Run manually: ${INSTALL_DIR}/start.sh"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "  ${GREEN}✅ MassMO Agent installed successfully!${NC}"
echo "  ─────────────────────────────────────────────────────"
echo "  Install dir : $INSTALL_DIR"
echo "  Agent ID    : $AGENT_ID"
echo "  Hub URL     : $HUB_URL"
echo "  Dashboard   : http://127.0.0.1:${AGENT_PORT}"
echo "  Logs        : $LOG_FILE"
echo ""
echo "  The agent starts automatically at login."
echo ""
