import logging

import httpx

logger = logging.getLogger(__name__)

# Local GoLogin Desktop API — runs on the same machine as the bot
GOLOGIN_LOCAL_URL = "http://localhost:36912"


class GoLoginService:
    async def start_profile(self, profile_id: str) -> dict:
        """Start GoLogin profile via local Desktop API. Returns response with wsUrl."""
        url = f"{GOLOGIN_LOCAL_URL}/browser/{profile_id}/start?sync=true"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    async def stop_profile(self, profile_id: str) -> None:
        """Stop GoLogin profile via local Desktop API."""
        url = f"{GOLOGIN_LOCAL_URL}/browser/{profile_id}/stop"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
