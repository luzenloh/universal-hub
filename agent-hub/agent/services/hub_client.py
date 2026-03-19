from __future__ import annotations
"""Agent → Hub communication: register and heartbeat."""
import asyncio
import logging

import httpx

from agent.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _client


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.hub_secret}"}


async def register(public_url: str, local_url: str) -> bool:
    """Register this agent with the Hub. Returns True on success."""
    url = f"{settings.hub_url}/hub/register"
    payload = {
        "agent_id": settings.agent_id,
        "public_url": public_url,
        "local_url": local_url,
        "owner_telegram_id": settings.owner_telegram_id or None,
    }
    try:
        client = _get_client()
        r = await client.post(url, json=payload, headers=_headers())
        r.raise_for_status()
        logger.info("Registered with Hub at %s", settings.hub_url)
        return True
    except Exception as exc:
        logger.error("Failed to register with Hub: %s", exc)
        return False


async def send_heartbeat(windows: list[dict]) -> None:
    """Send heartbeat with current window states to Hub."""
    url = f"{settings.hub_url}/hub/heartbeat"
    payload = {"agent_id": settings.agent_id, "windows": windows}
    try:
        client = _get_client()
        r = await client.post(url, json=payload, headers=_headers())
        r.raise_for_status()
    except Exception as exc:
        logger.warning("Heartbeat failed: %s", exc)


async def heartbeat_loop(interval: float = 10.0) -> None:
    """
    Background task: send heartbeat every `interval` seconds.
    Uses the global orchestrator to get current window states.
    """
    from bot.services.orchestrator import get_orchestrator

    while True:
        await asyncio.sleep(interval)
        try:
            orchestrator = get_orchestrator()
            states = orchestrator.get_all_states()
            windows = [s.model_dump() for s in states]
            await send_heartbeat(windows)
        except RuntimeError:
            # Orchestrator not yet initialized
            pass
        except Exception as exc:
            logger.warning("Heartbeat loop error: %s", exc)
