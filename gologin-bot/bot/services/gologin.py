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
        """Start multiple profiles concurrently. Returns list of results (or error dicts).
        If a profile returns empty wsUrl (already running), stops and restarts it to get a fresh wsUrl.
        """
        sem = asyncio.Semaphore(5)  # limit concurrent GoLogin Desktop calls

        async def _start(pid: str) -> dict:
            async with sem:
                try:
                    return await self.start_profile(pid)
                except Exception as e:
                    logger.error("Failed to start profile %s: %s", pid, e)
                    return {"error": str(e), "profileId": pid}

        results: list[dict] = list(await asyncio.gather(*[_start(pid) for pid in profile_ids]))

        # Profiles already running return empty wsUrl — stop and restart them
        stale = [
            pid for pid, r in zip(profile_ids, results)
            if isinstance(r, dict) and r.get("status") == "success" and not r.get("wsUrl")
        ]
        if stale:
            logger.info("Restarting %d already-running profiles to get fresh wsUrl: %s", len(stale), stale)
            await self.stop_profiles(stale)
            await asyncio.sleep(5)  # wait for GoLogin Desktop to fully close browsers
            retry: list[dict] = list(await asyncio.gather(*[_start(pid) for pid in stale]))
            # Second attempt for any that still fail
            still_bad = [pid for pid, r in zip(stale, retry) if "error" in r or not r.get("wsUrl")]
            if still_bad:
                logger.warning("Second restart attempt for %d profiles: %s", len(still_bad), still_bad)
                await asyncio.sleep(3)
                retry2: list[dict] = list(await asyncio.gather(*[_start(pid) for pid in still_bad]))
                retry_map2 = dict(zip(still_bad, retry2))
                retry = [retry_map2.get(pid, r) for pid, r in zip(stale, retry)]
            retry_map = dict(zip(stale, retry))
            results = [retry_map.get(pid, r) for pid, r in zip(profile_ids, results)]

        return results

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
            return data.get("profiles", data.get("payload", [])) if isinstance(data, dict) else []

    async def get_profile(self, profile_id: str) -> dict:
        """Fetch a single profile by ID."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{GOLOGIN_API_URL}/browser/{profile_id}",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def get_profiles_by_ids(self, profile_ids: list[str], delay: float = 1.0) -> dict[str, str]:
        """Fetch profiles sequentially with delay between requests. Returns {id: name} mapping."""
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
                        wait = 10 * (2 ** attempt)
                        logger.warning("Rate limited on %s, waiting %ds (attempt %d/4)", pid, wait, attempt + 1)
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
