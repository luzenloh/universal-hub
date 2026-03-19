# DEPRECATED — Phase 1 monolith entry point (bot + dashboard in one process).
# Phase 2: use hub_main.py (Hub) + agent_main.py (Local Agent) instead.
import asyncio
import html
import logging
import traceback

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, ErrorEvent

from bot.core.config import settings
from bot.db.base import async_session_factory, init_db
from bot.handlers import admin, common, shift
from bot.middlewares.db import DbSessionMiddleware
from bot.services.orchestrator import init_orchestrator
from bot.services.sync import sync_folders
from bot.services.ws_manager import WebSocketManager
from web.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    await init_db()
    logger.info("Database initialized")

    # Create shared ws_manager and orchestrator singleton (used by both bot and web panel)
    ws_manager = WebSocketManager()
    init_orchestrator(ws_manager)

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    @dp.errors()
    async def error_handler(event: ErrorEvent) -> None:
        tb = "".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__))
        logger.error("Unhandled exception:\n%s", tb)
        try:
            update = event.update
            chat_id = None
            if update.message:
                chat_id = update.message.chat.id
            elif update.callback_query and update.callback_query.message:
                chat_id = update.callback_query.message.chat.id
                await update.callback_query.answer("⚠️ Внутренняя ошибка. Смотри логи.", show_alert=True)
            if chat_id:
                short = html.escape(str(event.exception)[:200])
                await bot.send_message(chat_id, f"⚠️ <b>Ошибка:</b> <code>{short}</code>", parse_mode="HTML")
        except Exception:
            pass

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="folders", description="[Админ] Кто за какой папкой"),
            BotCommand(command="sync", description="[Админ] Синхронизировать папки из GoLogin"),
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )

    dp.update.middleware(DbSessionMiddleware(async_session_factory))

    dp.include_router(admin.router)
    dp.include_router(common.router)
    dp.include_router(shift.router)

    asyncio.create_task(dp.start_polling(bot))

    async def _sync_bg() -> None:
        try:
            await sync_folders(async_session_factory)
            logger.info("Folders synced from GoLogin")
        except Exception as exc:
            logger.warning("Background folder sync failed: %s", exc)

    asyncio.create_task(_sync_bg())

    fastapi_app = create_app(ws_manager)
    config = uvicorn.Config(
        fastapi_app,
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    logger.info("Starting web panel on http://%s:%d", settings.web_host, settings.web_port)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
