import logging

import httpx

logger = logging.getLogger(__name__)

GOLOGIN_API_URL = "https://api.gologin.com"


class GoLoginService:
    def __init__(self, api_token: str) -> None:
        self._headers = {"Authorization": f"Bearer {api_token}"}

    async def start_profile(self, profile_id: str) -> dict:
        """Launch GoLogin cloud profile. Returns response with WebSocket URL."""
        url = f"{GOLOGIN_API_URL}/browser/{profile_id}/web"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers=self._headers, json={})
            response.raise_for_status()
            return response.json()

    async def stop_profile(self, profile_id: str) -> None:
        """Stop GoLogin cloud profile."""
        url = f"{GOLOGIN_API_URL}/browser/{profile_id}/web"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(url, headers=self._headers)
            response.raise_for_status()
