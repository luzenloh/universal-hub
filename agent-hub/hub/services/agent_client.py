"""HTTP client for Hub → Agent communication."""
import logging

import httpx

from hub.core.config import settings
from web.models.schemas import AgentStartRequest

logger = logging.getLogger(__name__)

_TIMEOUT = 30


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.hub_secret}"}


def _agent_url(agent) -> str:
    """Pick public_url when available (Hub is remote), else local_url."""
    if agent.public_url:
        return agent.public_url.rstrip("/")
    return agent.local_url.rstrip("/")


async def start_shift(agent, payload: AgentStartRequest) -> bool:
    url = f"{_agent_url(agent)}/agent/start_shift"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=payload.model_dump(), headers=_headers())
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.error("agent start_shift failed (%s): %s", url, exc)
        return False


async def stop_shift(agent) -> bool:
    url = f"{_agent_url(agent)}/agent/stop_shift"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, headers=_headers())
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.error("agent stop_shift failed (%s): %s", url, exc)
        return False


async def get_status(agent) -> dict | None:
    url = f"{_agent_url(agent)}/agent/status"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=_headers())
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.warning("agent get_status failed: %s", exc)
        return None
