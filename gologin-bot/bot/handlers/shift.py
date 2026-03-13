import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.repository import TokenRepository
from bot.keyboards.builder import active_token_keyboard, main_menu_keyboard, token_list_keyboard

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "shift:start")
async def shift_start(callback: CallbackQuery, session: AsyncSession) -> None:
    repo = TokenRepository(session)
    tokens = await repo.get_free_tokens()

    if not tokens:
        await callback.answer("Нет свободных токенов, попробуй позже.", show_alert=True)
        return

    await callback.message.edit_text(  # type: ignore[union-attr]
        "Выбери профиль GoLogin:",
        reply_markup=token_list_keyboard(tokens),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shift:take:"))
async def shift_take_token(callback: CallbackQuery, session: AsyncSession) -> None:
    token_id = int(callback.data.split(":")[-1])  # type: ignore[union-attr]
    user_id = callback.from_user.id  # type: ignore[union-attr]

    repo = TokenRepository(session)
    token = await repo.assign_token(token_id, user_id)

    if token is None:
        # Token was taken by someone else — refresh the list
        tokens = await repo.get_free_tokens()
        if not tokens:
            await callback.message.edit_text(  # type: ignore[union-attr]
                "Токен уже занят. Свободных токенов больше нет, попробуй позже.",
                reply_markup=main_menu_keyboard(),
            )
        else:
            await callback.message.edit_text(  # type: ignore[union-attr]
                "Токен уже занят. Выбери другой:",
                reply_markup=token_list_keyboard(tokens),
            )
        await callback.answer("Токен уже занят!", show_alert=True)
        return

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"Токен назначен!\n\n"
        f"<b>{token.name}</b>\n<code>{token.value}</code>",
        parse_mode="HTML",
        reply_markup=active_token_keyboard(),
    )
    await callback.answer("Готово!")


@router.callback_query(F.data == "shift:release")
async def shift_release(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id  # type: ignore[union-attr]

    repo = TokenRepository(session)
    released = await repo.release_token(user_id)

    if released:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Токен освобождён. Хорошей работы!",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer("Токен освобождён.")
    else:
        await callback.answer("Активный токен не найден.", show_alert=True)
