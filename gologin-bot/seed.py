"""Seed test tokens into the database."""
import asyncio
import logging

from sqlalchemy import select

from bot.db.base import async_session_factory, init_db
from bot.db.models import Token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SEED_TOKENS = [
    {"name": "Profile 1", "value": "gl_token_xxxxxxxxxxxxxxxx_1"},
    {"name": "Profile 2", "value": "gl_token_xxxxxxxxxxxxxxxx_2"},
    {"name": "Profile 3", "value": "gl_token_xxxxxxxxxxxxxxxx_3"},
    {"name": "Profile 4", "value": "gl_token_xxxxxxxxxxxxxxxx_4"},
    {"name": "Profile 5", "value": "gl_token_xxxxxxxxxxxxxxxx_5"},
]


async def seed() -> None:
    await init_db()

    async with async_session_factory() as session:
        existing = await session.execute(select(Token))
        if existing.scalars().first():
            logger.info("Tokens already seeded, skipping.")
            return

        for data in SEED_TOKENS:
            session.add(Token(name=data["name"], value=data["value"]))

        await session.commit()
        logger.info("Seeded %d tokens.", len(SEED_TOKENS))


if __name__ == "__main__":
    asyncio.run(seed())
