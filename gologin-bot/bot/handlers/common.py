import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.repository import FolderRepository
from bot.keyboards.builder import active_folder_keyboard, main_menu_keyboard

logger = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    # Delete the /start command message to keep chat clean
    try:
        await message.delete()
    except Exception:
        pass

    user_id = message.from_user.id  # type: ignore[union-attr]
    repo = FolderRepository(session)
    folder = await repo.get_active_folder(user_id)

    if folder:
        await message.answer(
            f"Вы работаете с токеном:\n\n<b>{folder.name}</b>",
            parse_mode="HTML",
            reply_markup=active_folder_keyboard(),
        )
    else:
        await message.answer(
            "Добро пожаловать! Нажмите кнопку, чтобы начать смену.",
            reply_markup=main_menu_keyboard(),
        )
