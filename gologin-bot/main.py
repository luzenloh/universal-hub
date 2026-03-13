import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from bot.core.config import settings
from bot.db.base import async_session_factory, init_db
from bot.handlers import admin, common, shift
from bot.middlewares.db import DbSessionMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    await init_db()
    logger.info("Database initialized")

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="profiles", description="[Админ] Все профили и их статус"),
            BotCommand(command="setproxy", description="[Админ] /setproxy М1 http://user:pass@host:port"),
            BotCommand(command="clearproxy", description="[Админ] /clearproxy М1"),
            BotCommand(command="setua", description="[Админ] /setua М1 <user-agent>"),
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )

    dp.update.middleware(DbSessionMiddleware(async_session_factory))

    dp.include_router(admin.router)
    dp.include_router(common.router)
    dp.include_router(shift.router)

    logger.info("Starting bot polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
