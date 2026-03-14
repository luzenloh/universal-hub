import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

GOLOGIN_LOCAL_URL = "http://localhost:36912"
GOLOGIN_API_URL = "https://api.gologin.com"


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

    async def start_profiles(self, profile_ids: list[str]) -> list[dict]:
        """Start multiple profiles concurrently. Returns list of results (or error dicts)."""
        async def _start(pid: str) -> dict:
            try:
                return await self.start_profile(pid)
            except Exception as e:
                logger.error("Failed to start profile %s: %s", pid, e)
                return {"error": str(e), "profileId": pid}

        return await asyncio.gather(*[_start(pid) for pid in profile_ids])

    async def stop_profiles(self, profile_ids: list[str]) -> None:
        """Stop multiple profiles concurrently, ignoring errors."""
        async def _stop(pid: str) -> None:
            try:
                await self.stop_profile(pid)
            except Exception:
                pass

        await asyncio.gather(*[_stop(pid) for pid in profile_ids])


class GoLoginCloudService:
    """Communicates with GoLogin cloud API to fetch folders and profiles."""

    def __init__(self, api_token: str) -> None:
        self.headers = {"Authorization": f"Bearer {api_token}"}

    async def get_folders(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{GOLOGIN_API_URL}/folders", headers=self.headers)
            response.raise_for_status()
            data = response.json()
            return data.get("payload", data) if isinstance(data, dict) else data

    async def get_profiles_in_folder(self, folder_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{GOLOGIN_API_URL}/browser/v2",
                headers=self.headers,
                params={"folderId": folder_id, "limit": 50},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("payload", []) if isinstance(data, dict) else []
