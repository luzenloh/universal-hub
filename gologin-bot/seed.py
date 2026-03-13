"""Seed test tokens into the database."""
import asyncio
import logging

from sqlalchemy import select

from bot.db.base import async_session_factory, init_db
from bot.db.models import Token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SEED_TOKENS = [
    {"name": "М1",  "value": "695d30b833dfed868dd65120", "profile_id": "695d30b833dfed868dd65120"},
    {"name": "М2",  "value": "695d30c29eac628c3db71f82", "profile_id": "695d30c29eac628c3db71f82"},
    {"name": "М3",  "value": "695d30c78559c914a87ea2fc", "profile_id": "695d30c78559c914a87ea2fc"},
    {"name": "М4",  "value": "695d30cb33dfed868dd65b29", "profile_id": "695d30cb33dfed868dd65b29"},
    {"name": "М5",  "value": "695d30cfd948493885eacc06", "profile_id": "695d30cfd948493885eacc06"},
    {"name": "М6",  "value": "695d30d30827bbf446588009", "profile_id": "695d30d30827bbf446588009"},
    {"name": "М7",  "value": "695d30d890c828363e58a5b8", "profile_id": "695d30d890c828363e58a5b8"},
    {"name": "М8",  "value": "695d30dc75988e0a4703c83c", "profile_id": "695d30dc75988e0a4703c83c"},
    {"name": "М9",  "value": "695d30e06a1e707385c6bdcf", "profile_id": "695d30e06a1e707385c6bdcf"},
    {"name": "М10", "value": "695d30e4f227b608c8a8502d", "profile_id": "695d30e4f227b608c8a8502d"},
    {"name": "М11", "value": "695d32b2407c41b615c0d5c5", "profile_id": "695d32b2407c41b615c0d5c5"},
    {"name": "М12", "value": "695d32b7578d5faf9bae5cb2", "profile_id": "695d32b7578d5faf9bae5cb2"},
    {"name": "М13", "value": "695d32ba2518a2eb75cf5ffc", "profile_id": "695d32ba2518a2eb75cf5ffc"},
    {"name": "М14", "value": "695d32be90c828363e5b40f2", "profile_id": "695d32be90c828363e5b40f2"},
    {"name": "М15", "value": "695d32c19eac628c3dba23b8", "profile_id": "695d32c19eac628c3dba23b8"},
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
