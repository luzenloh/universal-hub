import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.repository import FolderRepository, UserRepository
from hub.keyboards.builder import active_folder_keyboard, main_menu_keyboard

logger = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    try:
        await message.delete()
    except Exception:
        pass

    user_id = message.from_user.id  # type: ignore[union-attr]

    # Auto-register user on first interaction
    await UserRepository(session).upsert(
        telegram_id=user_id,
        username=message.from_user.username,  # type: ignore[union-attr]
        first_name=message.from_user.first_name,  # type: ignore[union-attr]
    )

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
