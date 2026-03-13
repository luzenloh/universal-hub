"""Seed test tokens into the database."""
import asyncio
import logging

from sqlalchemy import select

from bot.db.base import async_session_factory, init_db
from bot.db.models import Token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SEED_TOKENS = [
    {"name": "Profile 1", "value": "gl_token_xxxxxxxxxxxxxxxx_1", "profile_id": None},
    {"name": "Profile 2", "value": "gl_token_xxxxxxxxxxxxxxxx_2", "profile_id": None},
    {"name": "Profile 3", "value": "gl_token_xxxxxxxxxxxxxxxx_3", "profile_id": None},
    {"name": "Profile 4", "value": "gl_token_xxxxxxxxxxxxxxxx_4", "profile_id": None},
    {"name": "Profile 5", "value": "gl_token_xxxxxxxxxxxxxxxx_5", "profile_id": None},
]


async def seed() -> None:
    await init_db()

    async with async_session_factory() as session:
        existing = await session.execute(select(Token))
        if existing.scalars().first():
            logger.info("Tokens already seeded, skipping.")
            return

        for data in SEED_TOKENS:
            session.add(Token(name=data["name"], value=data["value"], profile_id=data["profile_id"]))

        await session.commit()
        logger.info("Seeded %d tokens.", len(SEED_TOKENS))


if __name__ == "__main__":
    asyncio.run(seed())
