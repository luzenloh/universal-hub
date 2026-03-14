import logging

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

    async with session_factory() as session:
        repo = FolderRepository(session)

        for folder_data in folders:
            folder_id = folder_data.get("id") or folder_data.get("_id")
            folder_name = folder_data.get("name", "Без названия")
            if not folder_id:
                continue

            try:
                profiles = await cloud.get_profiles_in_folder(folder_id)
            except Exception as e:
                logger.error("Failed to fetch profiles for folder %s: %s", folder_name, e)
                profiles = []

            # Separate main (ТМ глав) from numbered (M1..M15)
            main_profile_id: str | None = None
            numbered: list[str] = []

            for p in profiles:
                pid = p.get("id") or p.get("_id", "")
                pname: str = p.get("name", "")
                if "глав" in pname.lower():
                    main_profile_id = pid
                else:
                    numbered.append((pname, pid))

            # Sort numbered profiles by name so M1 < M2 < ... < M15
            numbered.sort(key=lambda x: x[0])
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
                main_profile_id,
                len(numbered_ids),
            )
