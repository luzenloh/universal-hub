import logging
import re

from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.core.config import settings
from bot.db.repository import FolderRepository
from bot.services.gologin import GoLoginCloudService

logger = logging.getLogger(__name__)


async def sync_folders(session_factory: async_sessionmaker) -> None:
    """Sync GoLogin folders and their profiles into the local DB."""
    if not settings.gologin_api_token:
        logger.warning("GOLOGIN_API_TOKEN not set — skipping folder sync")
        return

    cloud = GoLoginCloudService(settings.gologin_api_token)

    try:
        folders = await cloud.get_folders()
    except Exception as e:
        logger.error("Failed to fetch GoLogin folders: %s", e)
        return

    logger.info("Fetched %d folders from GoLogin", len(folders))

    # Collect all profile IDs across all folders
    all_profile_ids: list[str] = []
    for folder_data in folders:
        all_profile_ids.extend(folder_data.get("associatedProfiles", []))

    # Fetch all profile names concurrently (one request per profile)
    try:
        id_to_name = await cloud.get_profiles_by_ids(all_profile_ids)
    except Exception as e:
        logger.error("Failed to fetch profile names: %s", e)
        id_to_name = {}

    logger.info("Fetched names for %d profiles", len(id_to_name))

    async with session_factory() as session:
        repo = FolderRepository(session)

        for folder_data in folders:
            folder_id = folder_data.get("id") or folder_data.get("_id")
            folder_name = folder_data.get("name", "Без названия")
            associated: list[str] = folder_data.get("associatedProfiles", [])

            if not folder_id:
                continue

            # Separate ТМ ГЛАВ from numbered profiles
            main_profile_id: str | None = None
            numbered: list[tuple[str, str]] = []  # (name, id)

            for pid in associated:
                pname = id_to_name.get(pid, "")
                pname_lower = pname.lower().strip()
                if pname_lower == "тм" or "глав" in pname_lower:
                    main_profile_id = pid
                else:
                    numbered.append((pname, pid))

            # Sort M1 < M2 < ... < M15 numerically (string sort would put M10 before M2)
            def _num_key(item: tuple[str, str]) -> int:
                m = re.search(r"\d+", item[0])
                return int(m.group()) if m else 0
            numbered.sort(key=_num_key)
            numbered_ids = [pid for _, pid in numbered]

            await repo.upsert_folder(
                gologin_id=folder_id,
                name=folder_name,
                main_profile_id=main_profile_id,
                numbered_profile_ids=numbered_ids,
            )
            logger.info(
                "Synced folder '%s': main=%s, numbered=%d",
                folder_name,
                "yes" if main_profile_id else "None",
                len(numbered_ids),
            )
