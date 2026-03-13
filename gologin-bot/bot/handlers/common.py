import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.repository import TokenRepository
from bot.keyboards.builder import active_token_keyboard, main_menu_keyboard

logger = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    repo = TokenRepository(session)
    user_id = message.from_user.id  # type: ignore[union-attr]

    token = await repo.get_active_token(user_id)
    if token:
        await message.answer(
            f"У вас активный токен:\n\n"
            f"<b>{token.name}</b>\n<code>{token.value}</code>",
            parse_mode="HTML",
            reply_markup=active_token_keyboard(token.profile_id),
        )
    else:
        await message.answer(
            "Добро пожаловать! Нажмите кнопку, чтобы начать смену.",
            reply_markup=main_menu_keyboard(),
        )
