"""
Hub entry point.

Runs:
  - aiogram bot (Telegram polling)
  - FastAPI Hub API on hub_port (register/heartbeat endpoints)
  - Background folder sync from GoLogin Cloud
"""
import asyncio
import html
import logging
import traceback
from contextlib import asynccontextmanager

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, ErrorEvent
from fastapi import FastAPI

from hub.api.routes import router as hub_router
from hub.core.config import settings
from hub.db.base import async_session_factory, init_db
from hub.handlers import admin, common, schedule, shift
from hub.middlewares.db import DbSessionMiddleware
from hub.services.sync import sync_folders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_hub_app(bot: Bot) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.bot = bot
        app.state.agent_prev_states: dict[str, dict[str, str]] = {}
        logger.info("Hub API ready on :%d", settings.hub_port)
        yield

    app = FastAPI(title="massmo-hub", lifespan=lifespan)
    app.include_router(hub_router)
    return app


async def main() -> None:
    await init_db()
    logger.info("Hub DB initialized")

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    @dp.errors()
    async def error_handler(event: ErrorEvent) -> None:
        tb = "".join(traceback.format_exception(
            type(event.exception), event.exception, event.exception.__traceback__
        ))
        logger.error("Unhandled exception:\n%s", tb)
        try:
            update = event.update
            chat_id = None
            if update.message:
                chat_id = update.message.chat.id
            elif update.callback_query and update.callback_query.message:
                chat_id = update.callback_query.message.chat.id
                await update.callback_query.answer(
                    "⚠️ Внутренняя ошибка. Смотри логи.", show_alert=True
                )
            if chat_id:
                short = html.escape(str(event.exception)[:200])
                await bot.send_message(
                    chat_id, f"⚠️ <b>Ошибка:</b> <code>{short}</code>", parse_mode="HTML"
                )
        except Exception:
            pass

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="folders", description="[Админ] Список папок"),
            BotCommand(command="sync", description="[Админ] Синхронизировать папки GoLogin"),
            BotCommand(command="agents", description="[Админ] Список агентов"),
            BotCommand(command="register_agent", description="[Админ] Создать токен установки агента"),
            BotCommand(command="revoke_agent", description="[Админ] Отозвать токен агента"),
            BotCommand(command="schedule", description="Проставить смены на неделю"),
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )

    dp.update.middleware(DbSessionMiddleware(async_session_factory))
    dp.include_router(admin.router)
    dp.include_router(common.router)
    dp.include_router(shift.router)
    dp.include_router(schedule.router)

    asyncio.create_task(dp.start_polling(bot))

    async def _sync_bg() -> None:
        try:
            await sync_folders(async_session_factory)
            logger.info("Folders synced from GoLogin")
        except Exception as exc:
            logger.warning("Background folder sync failed: %s", exc)

    asyncio.create_task(_sync_bg())

    hub_app = create_hub_app(bot)
    config = uvicorn.Config(
        hub_app,
        host=settings.hub_host,
        port=settings.hub_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    logger.info("Hub API at http://%s:%d", settings.hub_host, settings.hub_port)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
