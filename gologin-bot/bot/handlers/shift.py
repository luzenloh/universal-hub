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
    tokens = await repo.get_all_tokens()

    if not tokens:
        await callback.answer("Профили не найдены.", show_alert=True)
        return

    await callback.message.edit_text(  # type: ignore[union-attr]
        "Выбери профиль GoLogin:",
        reply_markup=token_list_keyboard(tokens),
    )
    await callback.answer()


@router.callback_query(F.data == "shift:taken")
async def shift_taken(callback: CallbackQuery) -> None:
    await callback.answer("Профиль занят, выбери другой.", show_alert=True)


@router.callback_query(F.data.startswith("shift:take:"))
async def shift_take_token(callback: CallbackQuery, session: AsyncSession) -> None:
    token_id = int(callback.data.split(":")[-1])  # type: ignore[union-attr]
    user_id = callback.from_user.id  # type: ignore[union-attr]

    repo = TokenRepository(session)
    token = await repo.assign_token(token_id, user_id)

    if token is None:
        # Token was taken by someone else between list render and click — refresh
        tokens = await repo.get_all_tokens()
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Профиль уже занят. Выбери другой:",
            reply_markup=token_list_keyboard(tokens),
        )
        await callback.answer("Профиль уже занят!", show_alert=True)
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
