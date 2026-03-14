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
    user_id = message.from_user.id  # type: ignore[union-attr]

    repo = FolderRepository(session)
    folder = await repo.get_active_folder(user_id)

    if folder:
        count_info = ""
        if folder.selected_count is not None:
            has_main = bool(folder.main_profile_id)
            count_info = f"\n🖥 Запущено: M1…M{folder.selected_count}"
            if has_main:
                count_info += " + ТМ глав"

        await message.answer(
            f"У вас активная папка:\n\n"
            f"<b>{folder.name}</b>{count_info}",
            parse_mode="HTML",
            reply_markup=active_folder_keyboard(),
        )
    else:
        await message.answer(
            "Добро пожаловать! Нажмите кнопку, чтобы начать смену.",
            reply_markup=main_menu_keyboard(),
        )
