from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from hub.core.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    from hub.db import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Safe migrations — ignored if columns already exist
        for table, col_def in (
            ("folders", "massmo_secrets TEXT DEFAULT '[]'"),
            ("folders", "assigned_agent_id TEXT"),
            ("agents", "owner_telegram_id INTEGER"),
            ("agents", "notify_chat_id INTEGER"),
            ("agents", "assigned_folder_id INTEGER"),
        ):
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_def}"))
            except Exception:
                pass
