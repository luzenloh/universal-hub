import asyncio
import logging
import traceback

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, ErrorEvent

from bot.core.config import ADMIN_USERNAME, settings
from bot.db.base import async_session_factory, init_db
from bot.handlers import admin, common, shift
from bot.middlewares.db import DbSessionMiddleware
from bot.services.sync import sync_folders

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    await init_db()
    logger.info("Database initialized")

    await sync_folders(async_session_factory)
    logger.info("Folders synced from GoLogin")

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    @dp.errors()
    async def error_handler(event: ErrorEvent) -> None:
        tb = "".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__))
        logger.error("Unhandled exception:\n%s", tb)
        try:
            # Find admin and notify
            async for tg_user in bot.get_chat_administrators(  # only works in groups
                chat_id=event.update.message.chat.id if event.update.message else 0
            ):
                pass
        except Exception:
            pass
        # Best-effort: reply to the user with a generic error
        try:
            update = event.update
            chat_id = None
            if update.message:
                chat_id = update.message.chat.id
            elif update.callback_query and update.callback_query.message:
                chat_id = update.callback_query.message.chat.id
                await update.callback_query.answer("⚠️ Внутренняя ошибка. Смотри логи.", show_alert=True)
            if chat_id:
                import html
                short = html.escape(str(event.exception)[:200])
                await bot.send_message(chat_id, f"⚠️ <b>Ошибка:</b> <code>{short}</code>", parse_mode="HTML")
        except Exception:
            pass

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
