"""GoLogin Cloud API — used by Hub only (no Desktop API)."""
import asyncio
import logging

import httpx

GOLOGIN_API_URL = "https://api.gologin.com"

logger = logging.getLogger(__name__)


class GoLoginCloudService:
    def __init__(self, api_token: str) -> None:
        self.headers = {"Authorization": f"Bearer {api_token}"}

    async def get_folders(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{GOLOGIN_API_URL}/folders", headers=self.headers)
            response.raise_for_status()
            data = response.json()
            return data.get("payload", data) if isinstance(data, dict) else data

    async def get_profile(self, profile_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{GOLOGIN_API_URL}/browser/{profile_id}", headers=self.headers
            )
            response.raise_for_status()
            return response.json()

    async def get_profiles_by_ids(
        self, profile_ids: list[str], delay: float = 1.0
    ) -> dict[str, str]:
        id_to_name: dict[str, str] = {}
        for pid in profile_ids:
            for attempt in range(4):
                try:
                    p = await self.get_profile(pid)
                    id_to_name[pid] = p.get("name", "")
                    await asyncio.sleep(delay)
                    break
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        wait = 10 * (2**attempt)
                        logger.warning(
                            "Rate limited on %s, waiting %ds (attempt %d/4)",
                            pid, wait, attempt + 1,
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.warning("Failed to fetch profile %s: %s", pid, e)
                        id_to_name[pid] = ""
                        break
                except Exception as e:
                    logger.warning("Failed to fetch profile %s: %s", pid, e)
                    id_to_name[pid] = ""
                    break
        return id_to_name
