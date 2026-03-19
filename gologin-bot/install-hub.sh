#!/usr/bin/env bash
# install-hub.sh — MassMO Hub installer (Linux VPS)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install-hub.sh | bash
#   ./install-hub.sh
#
# What it does:
#   1. Checks / installs Docker + Docker Compose
#   2. Downloads docker-compose.yml and .env.hub template
#   3. Interactive setup: prompts for BOT_TOKEN, HUB_SECRET, etc.
#   4. docker-compose up -d
#
# Requirements: bash, curl, sudo access
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

GITHUB_REPO="luzenloh/universal-hub"
GITHUB_SUBDIR="gologin-bot"
INSTALL_DIR="${HOME}/gologin-hub"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
prompt() {
    # prompt <variable_name> <display_name> [default]
    local var="$1" label="$2" default="${3:-}"
    if [[ -n "$default" ]]; then
        read -rp "  ${label} [${default}]: " val
        val="${val:-$default}"
    else
        read -rp "  ${label}: " val
        while [[ -z "$val" ]]; do
            echo "  (required)"
            read -rp "  ${label}: " val
        done
    fi
    printf -v "$var" '%s' "$val"
}

echo ""
echo -e "  ${BOLD}${CYAN}MassMO Hub Installer${NC}"
echo "  ─────────────────────────────────────────────────────"
echo ""

# ── Step 1: Docker ────────────────────────────────────────────────────────────
info "Checking Docker..."

if ! command -v docker &>/dev/null; then
    info "Docker not found. Installing via get.docker.com..."
    curl -fsSL https://get.docker.com | sh
    # Add current user to docker group (takes effect after re-login or newgrp)
    sudo usermod -aG docker "$USER" 2>/dev/null || true
    ok "Docker installed"
else
    ok "Docker: $(docker --version)"
fi

if ! docker compose version &>/dev/null && ! docker-compose version &>/dev/null; then
    info "Installing Docker Compose plugin..."
    sudo apt-get install -y docker-compose-plugin 2>/dev/null \
        || sudo yum install -y docker-compose-plugin 2>/dev/null \
        || warn "Could not auto-install docker-compose-plugin. Install manually."
fi

COMPOSE_CMD="docker compose"
if ! docker compose version &>/dev/null; then
    COMPOSE_CMD="docker-compose"
fi

ok "Compose: $($COMPOSE_CMD version --short 2>/dev/null || echo 'found')"

# ── Step 2: Download files ────────────────────────────────────────────────────
info "Downloading Hub files..."
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

BASE_URL="https://raw.githubusercontent.com/${GITHUB_REPO}/main/${GITHUB_SUBDIR}"

# Try GitHub first, fall back to local copy
download_or_copy() {
    local file="$1"
    if curl -fsSL "${BASE_URL}/${file}" -o "$file" 2>/dev/null; then
        ok "Downloaded $file"
    else
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        if [[ -f "${SCRIPT_DIR}/${file}" ]]; then
            cp "${SCRIPT_DIR}/${file}" .
            ok "Copied $file from local directory"
        else
            warn "Could not download $file — create it manually."
        fi
    fi
}

download_or_copy "docker-compose.yml"
download_or_copy "hub_main.py"

# Download hub/ and bot/ directories if not present
if [[ ! -d "hub" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -d "${SCRIPT_DIR}/hub" ]]; then
        cp -r "${SCRIPT_DIR}/hub" .
        cp -r "${SCRIPT_DIR}/web" . 2>/dev/null || true
        cp -r "${SCRIPT_DIR}/bot" . 2>/dev/null || true
        cp -r "${SCRIPT_DIR}/requirements.txt" . 2>/dev/null || true
        ok "Source files copied from local directory"
    else
        warn "Hub source not found. Download the full release from GitHub."
    fi
fi

# ── Step 3: Interactive .env.hub setup ────────────────────────────────────────
if [[ -f ".env.hub" ]]; then
    warn ".env.hub already exists."
    read -rp "  Overwrite? [y/N]: " overwrite
    if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
        info "Keeping existing .env.hub"
        SKIP_ENV=true
    fi
fi

if [[ "${SKIP_ENV:-false}" != "true" ]]; then
    echo ""
    echo -e "  ${BOLD}Hub configuration${NC}"
    echo "  ─────────────────────────────────────────────────────"
    echo "  Press Enter to use the default value [shown in brackets]."
    echo ""

    prompt BOT_TOKEN      "Telegram Bot Token (from @BotFather)"
    prompt ADMIN_USERNAME "Admin Telegram username (without @)"
    prompt HUB_SECRET     "Hub secret key (random string, e.g. $(openssl rand -hex 16))"
    prompt GOLOGIN_TOKEN  "GoLogin API token (optional, press Enter to skip)" ""
    prompt HUB_PUBLIC_URL "Hub public URL (e.g. https://yourdomain.com or http://IP:8082)" "http://127.0.0.1:8082"

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
    ok ".env.hub written (permissions: 600)"
fi

# ── Step 4: docker-compose.yml fallback ───────────────────────────────────────
if [[ ! -f "docker-compose.yml" ]]; then
    info "Creating minimal docker-compose.yml..."
    cat > docker-compose.yml <<'YAML'
version: "3.9"

services:
  hub:
    build: .
    container_name: massmo-hub
    restart: unless-stopped
    ports:
      - "8082:8082"
    env_file:
      - .env.hub
    volumes:
      - ./hub.db:/app/hub.db
    command: python3 hub_main.py
YAML
    ok "docker-compose.yml created"
fi

# ── Step 5: Start Hub ─────────────────────────────────────────────────────────
info "Starting Hub..."

# Pull / build
$COMPOSE_CMD pull 2>/dev/null || true
$COMPOSE_CMD up -d --build

echo ""
echo -e "  ${GREEN}${BOLD}✅ MassMO Hub started!${NC}"
echo "  ─────────────────────────────────────────────────────"
echo "  Install dir: $INSTALL_DIR"
echo "  Hub API:     http://0.0.0.0:8082"
echo ""
echo "  Useful commands:"
echo "    $COMPOSE_CMD logs -f hub        # live logs"
echo "    $COMPOSE_CMD restart hub        # restart"
echo "    $COMPOSE_CMD down               # stop"
echo ""
echo "  Next steps:"
echo "    1. Make sure port 8082 is open in your firewall"
echo "    2. Register agents: /register_agent <username> in Telegram"
echo ""
