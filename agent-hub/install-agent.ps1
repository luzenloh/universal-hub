# install-agent.ps1 — MassMO Agent installer (Windows)
#
# Usage (PowerShell):
#   irm https://raw.githubusercontent.com/OWNER/REPO/main/install-agent.ps1 | iex; Install-Agent 'GLAGENT_...'
#
#   Or save locally and run:
#   .\install-agent.ps1 -SetupToken 'GLAGENT_...'
#
# What it does:
#   1. Checks / installs Python 3.10+ (via winget)
#   2. Checks / installs uv
#   3. Downloads the latest agent release from GitHub
#   4. Decodes the setup token → calls /hub/claim to get .env.agent config
#   5. Writes .env.agent
#   6. Registers a Windows Task Scheduler entry (auto-start at login)
#   7. Starts the agent
#
# Requirements: PowerShell 5.1+, internet access
# ─────────────────────────────────────────────────────────────────────────────

param(
    [string]$SetupToken = ""
)

# Allow calling as: ... | iex; Install-Agent 'GLAGENT_...'
function Install-Agent {
    param([string]$Token)
    $script:SetupToken = $Token
    Main
}

# ── Config ────────────────────────────────────────────────────────────────────
$GITHUB_REPO  = "luzenloh/universal-hub"
$GITHUB_SUBDIR = "agent-hub"
$INSTALL_DIR  = "$env:APPDATA\gologin-agent"
$TASK_NAME    = "MassMO-GoLogin-Agent"
$LOG_FILE     = "$env:TEMP\gologin-agent.log"
$AGENT_PORT   = 8081

# ── Helpers ───────────────────────────────────────────────────────────────────
function Write-Info  { param([string]$Msg) Write-Host "[INFO]  $Msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$Msg) Write-Host "[OK]    $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "[WARN]  $Msg" -ForegroundColor Yellow }
function Write-Error2 { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red; exit 1 }

function Decode-Base64Url {
    param([string]$B64)
    $B64 = $B64.Replace('-', '+').Replace('_', '/')
    switch ($B64.Length % 4) {
        2 { $B64 += "==" }
        3 { $B64 += "=" }
    }
    [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($B64))
}

# ── Main ──────────────────────────────────────────────────────────────────────
function Main {
    Write-Host ""
    Write-Host "  MassMO Agent Installer (Windows)" -ForegroundColor Cyan
    Write-Host "  ─────────────────────────────────────────────────────"
    Write-Host ""

    # ── Validate token ────────────────────────────────────────────────────────
    if ([string]::IsNullOrWhiteSpace($SetupToken)) {
        Write-Host ""
        Write-Host "  Usage: .\install-agent.ps1 -SetupToken GLAGENT_..." -ForegroundColor Yellow
        Write-Host "  Get the token from admin via Telegram: /register_agent <your_username>"
        Write-Host ""
        exit 1
    }

    if (-not $SetupToken.StartsWith("GLAGENT_")) {
        Write-Error2 "Invalid token format. Expected GLAGENT_..."
    }

    # ── Step 1: Python 3.10+ ──────────────────────────────────────────────────
    Write-Info "Checking Python 3.10+..."
    $Python = $null

    foreach ($cmd in @("python3.12", "python3.11", "python3.10", "python3", "python")) {
        try {
            $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($ver -match "^3\.(1[0-9]|[2-9]\d)") {
                $Python = $cmd
                Write-Ok "Found $cmd ($ver)"
                break
            }
        } catch {}
    }

    if (-not $Python) {
        Write-Warn "Python 3.10+ not found. Attempting install via winget..."
        try {
            winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
            # Refresh PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("Path", "User")
            $Python = "python"
            Write-Ok "Python installed via winget"
        } catch {
            Write-Host ""
            Write-Host "  Please install Python 3.10+ manually:" -ForegroundColor Yellow
            Write-Host "  https://www.python.org/downloads/" -ForegroundColor Yellow
            Write-Host "  Make sure to check 'Add Python to PATH' during installation."
            Write-Host ""
            Write-Error2 "Python 3.10+ required."
        }
    }

    # ── Step 2: uv ────────────────────────────────────────────────────────────
    Write-Info "Checking uv..."
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Info "Installing uv..."
        try {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
            # Refresh PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" +
                        "$env:USERPROFILE\.cargo\bin"
        } catch {
            Write-Error2 "Failed to install uv: $_"
        }
    }
    $UvCmd = if (Get-Command uv -ErrorAction SilentlyContinue) { "uv" }
             elseif (Test-Path "$env:USERPROFILE\.cargo\bin\uv.exe") { "$env:USERPROFILE\.cargo\bin\uv.exe" }
             elseif (Test-Path "$env:LOCALAPPDATA\uv\bin\uv.exe") { "$env:LOCALAPPDATA\uv\bin\uv.exe" }
             else { Write-Error2 "uv not found after install." }

    Write-Ok "uv: $(& $UvCmd --version 2>$null)"

    # ── Step 3: Download agent ────────────────────────────────────────────────
    Write-Info "Downloading latest agent release..."
    New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null

    $ReleaseUrl = "https://github.com/$GITHUB_REPO/releases/latest/download/agent.zip"
    $RawBase = "https://raw.githubusercontent.com/$GITHUB_REPO/main/$GITHUB_SUBDIR"
    $ZipPath = "$env:TEMP\agent.zip"

    $Downloaded = $false
    try {
        Invoke-WebRequest -Uri $ReleaseUrl -OutFile $ZipPath -UseBasicParsing
        Expand-Archive -Path $ZipPath -DestinationPath "$env:TEMP\agent-extract" -Force
        # Copy contents (strip top-level dir if present)
        $ExtractedItems = Get-ChildItem "$env:TEMP\agent-extract"
        if ($ExtractedItems.Count -eq 1 -and $ExtractedItems[0].PSIsContainer) {
            Copy-Item -Path "$($ExtractedItems[0].FullName)\*" -Destination $INSTALL_DIR -Recurse -Force
        } else {
            Copy-Item -Path "$env:TEMP\agent-extract\*" -Destination $INSTALL_DIR -Recurse -Force
        }
        Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
        Remove-Item "$env:TEMP\agent-extract" -Recurse -Force -ErrorAction SilentlyContinue
        $Downloaded = $true
        Write-Ok "Agent downloaded to $INSTALL_DIR"
    } catch {
        Write-Warn "GitHub release not available. Checking current directory..."
    }

    if (-not $Downloaded) {
        $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
        if (Test-Path "$ScriptDir\agent_main.py") {
            Write-Info "Copying from current directory..."
            Get-ChildItem -Path $ScriptDir | Where-Object {
                $_.Name -notin @('.env.hub', '.env.agent', '.env', 'hub.db', '.venv', '__pycache__')
            } | Copy-Item -Destination $INSTALL_DIR -Recurse -Force
            Write-Ok "Agent copied from $ScriptDir"
        } else {
            Write-Error2 "Could not download agent.`n  Release URL: $ReleaseUrl`n  And no local agent_main.py found."
        }
    }

    # ── Step 4: Decode token + claim config ───────────────────────────────────
    Write-Info "Decoding setup token..."
    $B64Part = $SetupToken.Substring("GLAGENT_".Length)

    try {
        $JsonStr = Decode-Base64Url $B64Part
        $Payload = $JsonStr | ConvertFrom-Json
        $HubUrl  = $Payload.hub_url
        $Jti     = $Payload.jti
    } catch {
        Write-Error2 "Failed to decode setup token: $_"
    }

    Write-Info "Hub URL: $HubUrl"
    Write-Info "Claiming config from Hub..."

    try {
        $ClaimResp = Invoke-RestMethod -Uri "$HubUrl/hub/claim/$Jti" -Method Get -UseBasicParsing
    } catch {
        Write-Error2 "Failed to claim config from Hub.`n  URL: $HubUrl/hub/claim/$Jti`n  Error: $_`n  Tokens are single-use and valid for 7 days."
    }

    $HubSecret      = $ClaimResp.hub_secret
    $AgentId        = $ClaimResp.agent_id
    $OwnerTgId      = $ClaimResp.owner_telegram_id
    $AgentPort      = if ($ClaimResp.agent_port) { $ClaimResp.agent_port } else { 8081 }

    Write-Ok "Config received: agent_id=$AgentId"

    # ── Step 5: Write .env.agent ──────────────────────────────────────────────
    Write-Info "Writing .env.agent..."
    $EnvContent = @"
HUB_URL=$HubUrl
HUB_SECRET=$HubSecret
AGENT_ID=$AgentId
OWNER_TELEGRAM_ID=$OwnerTgId
AGENT_PORT=$AgentPort
AGENT_HOST=127.0.0.1
"@
    Set-Content -Path "$INSTALL_DIR\.env.agent" -Value $EnvContent -Encoding UTF8
    Write-Ok ".env.agent written"

    # ── Step 6: Install dependencies ─────────────────────────────────────────
    Write-Info "Installing Python dependencies..."
    Push-Location $INSTALL_DIR
    try {
        & $UvCmd pip install -r requirements-agent.txt --python $Python -q
        if ($LASTEXITCODE -ne 0) { Write-Error2 "Dependency installation failed." }
        Write-Ok "Dependencies installed"
    } finally {
        Pop-Location
    }

    # ── Step 7: Create start script ───────────────────────────────────────────
    $StartScript = "$INSTALL_DIR\start.bat"
    @"
@echo off
cd /d "$INSTALL_DIR"
$UvCmd run --python $Python agent_main.py >> "$LOG_FILE" 2>&1
"@ | Set-Content -Path $StartScript -Encoding ASCII

    # ── Step 8: Register Task Scheduler ──────────────────────────────────────
    Write-Info "Registering Windows Task Scheduler entry..."

    $Action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$StartScript`""
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
                    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
                    -MultipleInstances IgnoreNew

    # Remove old task if exists
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue

    Register-ScheduledTask -TaskName $TASK_NAME -Action $Action -Trigger $Trigger `
        -Settings $Settings -RunLevel Limited -Force | Out-Null

    Write-Ok "Task Scheduler entry registered: $TASK_NAME"

    # ── Step 9: Start agent ───────────────────────────────────────────────────
    Write-Info "Starting agent..."
    Start-ScheduledTask -TaskName $TASK_NAME
    Start-Sleep -Seconds 2

    # ── Done ──────────────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host "  ✅ MassMO Agent installed successfully!" -ForegroundColor Green
    Write-Host "  ─────────────────────────────────────────────────────"
    Write-Host "  Install dir : $INSTALL_DIR"
    Write-Host "  Agent ID    : $AgentId"
    Write-Host "  Hub URL     : $HubUrl"
    Write-Host "  Dashboard   : http://127.0.0.1:$AgentPort"
    Write-Host "  Logs        : $LOG_FILE"
    Write-Host ""
    Write-Host "  The agent starts automatically at login."
    Write-Host "  To stop:  Stop-ScheduledTask -TaskName '$TASK_NAME'"
    Write-Host "  To start: Start-ScheduledTask -TaskName '$TASK_NAME'"
    Write-Host ""
}

# Auto-run if called with -SetupToken param directly
if ($SetupToken -ne "") {
    Main
}
