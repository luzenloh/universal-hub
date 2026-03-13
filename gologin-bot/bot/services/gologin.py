import logging

import httpx

logger = logging.getLogger(__name__)

GOLOGIN_LOCAL_URL = "http://localhost:36912"


class GoLoginService:
    def __init__(self, api_token: str) -> None:
        self._headers = {"Authorization": f"Bearer {api_token}"}

    async def start_profile(self, profile_id: str) -> dict:
        """Launch profile via GoLogin Desktop app (must be running). Returns wsUrl."""
        url = f"{GOLOGIN_LOCAL_URL}/browser/{profile_id}/start?sync=true"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url, headers=self._headers)
            response.raise_for_status()
            return response.json()

    async def stop_profile(self, profile_id: str) -> None:
        """Stop profile via GoLogin Desktop app."""
        url = f"{GOLOGIN_LOCAL_URL}/browser/{profile_id}/stop"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=self._headers)
            response.raise_for_status()
