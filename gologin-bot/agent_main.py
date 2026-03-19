"""
Agent entry point.

Flow:
  1. Kill old instance via PID file + free port
  2. Write own PID file
  3. Start tunnel supervisor (auto-restarts tunnel, re-registers with Hub on each new URL)
  4. Start uvicorn on agent_port (dashboard + agent API)
  5. Start heartbeat loop (every 10s)
  6. restore_from_cache() is called inside web/app.py lifespan
"""
import asyncio
import logging
import os
import signal
import time
from pathlib import Path

_VERSION_FILE = Path(__file__).parent / "VERSION"
VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "unknown"

# bot.core.config requires BOT_TOKEN/ADMIN_USERNAME via pydantic-settings;
# the Agent doesn't use these — set defaults before any bot.* imports.
os.environ.setdefault("BOT_TOKEN", "placeholder-not-used-by-agent")
os.environ.setdefault("ADMIN_USERNAME", "placeholder")

import uvicorn

from agent.core.config import settings as agent_settings
from agent.services import hub_client
from agent.services.tunnel import keep_tunnel_alive
from bot.services.orchestrator import init_orchestrator
from bot.services.ws_manager import WebSocketManager
from web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_PID_FILE = Path("/tmp/massmo-agent.pid")


def _kill_old_instance() -> None:
    """Kill any previously running agent via PID file."""
    if not _PID_FILE.exists():
        return
    try:
        old_pid = int(_PID_FILE.read_text().strip())
        if old_pid == os.getpid():
            return
        logger.info("Stopping old agent instance (PID %d)…", old_pid)
        os.kill(old_pid, signal.SIGTERM)
        time.sleep(2)
        try:
            os.kill(old_pid, signal.SIGKILL)  # force if still alive
        except ProcessLookupError:
            pass
        logger.info("Old instance stopped.")
    except (ValueError, ProcessLookupError, OSError):
        pass
    finally:
        _PID_FILE.unlink(missing_ok=True)


def _free_port(port: int) -> None:
    """Kill any process still holding the agent port."""
    import subprocess
    try:
        out = subprocess.check_output(["lsof", "-ti", f":{port}"], text=True).strip()
        for pid_str in out.split():
            pid = int(pid_str)
            if pid != os.getpid():
                os.kill(pid, signal.SIGKILL)
                logger.info("Freed port %d (killed PID %d)", port, pid)
    except (subprocess.CalledProcessError, ValueError, OSError):
        pass


async def _check_for_update() -> None:
    """Warn if a newer version is available on GitHub. Non-blocking."""
    github_repo = os.environ.get("GITHUB_REPO", "")
    if not github_repo:
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code != 200:
                return
            latest = resp.json().get("tag_name", "").lstrip("v")
            if latest and latest != VERSION:
                logger.warning(
                    "New version available: %s (running %s). Run install-agent.sh to update.",
                    latest, VERSION,
                )
    except Exception:
        pass  # network unavailable — ignore


async def main() -> None:
    logger.info("MassMO Agent v%s", VERSION)
    local_url = f"http://{agent_settings.agent_host}:{agent_settings.agent_port}"

    # Init orchestrator + web app
    ws_manager = WebSocketManager()
    init_orchestrator(ws_manager)
    fastapi_app = create_app(ws_manager, hub_secret=agent_settings.hub_secret)

    async def _on_tunnel_url(url: str) -> None:
        """Called on every new tunnel URL — startup and after each restart."""
        logger.info("Tunnel URL: %s", url)
        await hub_client.register(public_url=url, local_url=local_url)

    # Tunnel supervisor: auto-restarts cloudflared if it dies,
    # calls _on_tunnel_url to re-register with Hub every time.
    asyncio.create_task(
        keep_tunnel_alive(agent_settings.agent_port, on_new_url=_on_tunnel_url),
        name="tunnel-supervisor",
    )

    asyncio.create_task(_check_for_update(), name="update-check")

    # Heartbeat loop
    asyncio.create_task(hub_client.heartbeat_loop(interval=10.0), name="heartbeat")

    # Serve
    config = uvicorn.Config(
        fastapi_app,
        host=agent_settings.agent_host,
        port=agent_settings.agent_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    logger.info(
        "Agent dashboard at http://%s:%d",
        agent_settings.agent_host,
        agent_settings.agent_port,
    )
    await server.serve()


if __name__ == "__main__":
    _kill_old_instance()
    _free_port(agent_settings.agent_port)
    _PID_FILE.write_text(str(os.getpid()))
    try:
        asyncio.run(main())
    finally:
        _PID_FILE.unlink(missing_ok=True)
