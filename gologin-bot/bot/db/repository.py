import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Token

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
