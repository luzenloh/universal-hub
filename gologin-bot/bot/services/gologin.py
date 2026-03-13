import logging

import httpx

logger = logging.getLogger(__name__)

GOLOGIN_LOCAL_URL = "http://localhost:36912"


class GoLoginService:
    """Communicates with the GoLogin Desktop app local API (no auth required)."""

    async def start_profile(self, profile_id: str) -> dict:
        """Start profile via GoLogin Desktop. Returns dict with wsUrl."""
        url = f"{GOLOGIN_LOCAL_URL}/browser/start-profile"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json={"profileId": profile_id, "sync": True})
            response.raise_for_status()
            return response.json()

    async def stop_profile(self, profile_id: str) -> None:
        """Stop profile via GoLogin Desktop."""
        url = f"{GOLOGIN_LOCAL_URL}/browser/stop-profile"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={"profileId": profile_id})
            response.raise_for_status()
