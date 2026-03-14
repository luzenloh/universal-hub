import json
import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Folder, Token

logger = logging.getLogger(__name__)


class TokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_token(self, user_id: int) -> Token | None:
        result = await self.session.execute(
            select(Token).where(Token.assigned_to == user_id, Token.is_free == False)  # noqa: E712
        )
        return result.scalar_one_or_none()

    async def get_token_by_id(self, token_id: int) -> Token | None:
        result = await self.session.execute(select(Token).where(Token.id == token_id))
        return result.scalar_one_or_none()

    async def get_all_tokens(self) -> list[Token]:
        result = await self.session.execute(select(Token).order_by(Token.id))
        return list(result.scalars().all())

    async def assign_token(self, token_id: int, user_id: int) -> Token | None:
        """Atomically assign a token. Returns Token if successful, None if already taken."""
        result = await self.session.execute(
            update(Token)
            .where(Token.id == token_id, Token.is_free == True)  # noqa: E712
            .values(is_free=False, assigned_to=user_id, assigned_at=datetime.utcnow())
            .returning(Token)
        )
        await self.session.commit()
        row = result.fetchone()
        if row is None:
            return None
        return row[0]

    async def force_release_token(self, token_id: int) -> bool:
        """Admin: release a specific token by ID regardless of who holds it."""
        result = await self.session.execute(
            update(Token)
            .where(Token.id == token_id, Token.is_free == False)  # noqa: E712
            .values(is_free=True, assigned_to=None, assigned_at=None)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def release_token(self, user_id: int) -> bool:
        """Release token assigned to user. Returns True if a token was released."""
        result = await self.session.execute(
            update(Token)
            .where(Token.assigned_to == user_id)
            .values(is_free=True, assigned_to=None, assigned_at=None)
        )
        await self.session.commit()
        return result.rowcount > 0


class FolderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all_folders(self) -> list[Folder]:
        result = await self.session.execute(select(Folder).order_by(Folder.name))
        return list(result.scalars().all())

    async def get_folder_by_id(self, folder_id: int) -> Folder | None:
        result = await self.session.execute(select(Folder).where(Folder.id == folder_id))
        return result.scalar_one_or_none()

    async def get_active_folder(self, user_id: int) -> Folder | None:
        result = await self.session.execute(
            select(Folder).where(Folder.assigned_to == user_id, Folder.is_free == False)  # noqa: E712
        )
        return result.scalar_one_or_none()

    async def assign_folder(self, folder_id: int, user_id: int, count: int) -> Folder | None:
        """Atomically assign a folder. Returns Folder if successful, None if already taken."""
        result = await self.session.execute(
            update(Folder)
            .where(Folder.id == folder_id, Folder.is_free == True)  # noqa: E712
            .values(is_free=False, assigned_to=user_id, assigned_at=datetime.utcnow(), selected_count=count)
            .returning(Folder)
        )
        await self.session.commit()
        row = result.fetchone()
        return row[0] if row else None

    async def release_folder(self, user_id: int) -> Folder | None:
        """Release folder assigned to user. Returns the folder if released."""
        folder = await self.get_active_folder(user_id)
        if not folder:
            return None
        await self.session.execute(
            update(Folder)
            .where(Folder.assigned_to == user_id)
            .values(is_free=True, assigned_to=None, assigned_at=None, selected_count=None)
        )
        await self.session.commit()
        return folder

    async def force_release_folder(self, folder_id: int) -> bool:
        result = await self.session.execute(
            update(Folder)
            .where(Folder.id == folder_id, Folder.is_free == False)  # noqa: E712
            .values(is_free=True, assigned_to=None, assigned_at=None, selected_count=None)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def upsert_folder(
        self,
        gologin_id: str,
        name: str,
        main_profile_id: str | None,
        numbered_profile_ids: list[str],
    ) -> None:
        result = await self.session.execute(select(Folder).where(Folder.gologin_id == gologin_id))
        folder = result.scalar_one_or_none()
        numbered_json = json.dumps(numbered_profile_ids)
        if folder:
            folder.name = name
            folder.main_profile_id = main_profile_id
            folder.numbered_profile_ids = numbered_json
            folder.profile_count = len(numbered_profile_ids)
        else:
            self.session.add(Folder(
                gologin_id=gologin_id,
                name=name,
                main_profile_id=main_profile_id,
                numbered_profile_ids=numbered_json,
                profile_count=len(numbered_profile_ids),
            ))
        await self.session.commit()
