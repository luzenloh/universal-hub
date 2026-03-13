import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from bot.core.config import ADMIN_USERNAME, settings
from bot.db.repository import TokenRepository
from bot.keyboards.builder import active_token_keyboard, main_menu_keyboard, token_info_keyboard, token_list_keyboard
from bot.services.browser import BrowserService
from bot.services.gologin import GoLoginService

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


@router.callback_query(F.data.startswith("shift:info:"))
async def shift_token_info(callback: CallbackQuery, session: AsyncSession) -> None:
    token_id = int(callback.data.split(":")[-1])  # type: ignore[union-attr]

    repo = TokenRepository(session)
    token = await repo.get_token_by_id(token_id)

    if not token or token.is_free:
        await callback.answer("Профиль уже освобождён.", show_alert=True)
        return

    holder_name = "неизвестен"
    if token.assigned_to:
        try:
            chat = await callback.bot.get_chat(token.assigned_to)  # type: ignore[union-attr]
            parts = []
            if chat.first_name:
                parts.append(chat.first_name)
            if chat.last_name:
                parts.append(chat.last_name)
            holder_name = " ".join(parts) if parts else holder_name
            if chat.username:
                holder_name += f" (@{chat.username})"
        except Exception:
            holder_name = str(token.assigned_to)

    since = ""
    if token.assigned_at:
        since = f"\n🕐 Начало сессии: {token.assigned_at.strftime('%d.%m.%Y %H:%M')} UTC"

    is_admin = callback.from_user.username == ADMIN_USERNAME  # type: ignore[union-attr]

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"🔒 Профиль <b>{token.name}</b> занят\n\n"
        f"👤 Кто занял: {holder_name}"
        f"{since}",
        parse_mode="HTML",
        reply_markup=token_info_keyboard(token_id, is_admin=is_admin),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shift:force_release:"))
async def shift_force_release(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.from_user.username != ADMIN_USERNAME:  # type: ignore[union-attr]
        await callback.answer("Нет прав.", show_alert=True)
        return

    token_id = int(callback.data.split(":")[-1])  # type: ignore[union-attr]

    repo = TokenRepository(session)
    token = await repo.get_token_by_id(token_id)
    token_name = token.name if token else str(token_id)

    released = await repo.force_release_token(token_id)
    if released:
        tokens = await repo.get_all_tokens()
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"✅ Профиль <b>{token_name}</b> принудительно освобождён.\n\nВыбери профиль GoLogin:",
            parse_mode="HTML",
            reply_markup=token_list_keyboard(tokens),
        )
        await callback.answer("Освобождено.")
    else:
        await callback.answer("Токен уже был свободен.", show_alert=True)


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
        reply_markup=active_token_keyboard(token.profile_id),
    )
    await callback.answer("Готово!")


@router.callback_query(F.data == "shift:launch")
async def shift_launch_profile(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id  # type: ignore[union-attr]

    repo = TokenRepository(session)
    token = await repo.get_active_token(user_id)

    if not token:
        await callback.answer("Активный токен не найден.", show_alert=True)
        return

    # GoLogin Desktop launch if profile_id is set
    if token.profile_id:
        await callback.answer("Запускаем GoLogin профиль…")
        try:
            service = GoLoginService()
            result = await service.start_profile(token.profile_id)
            ws_url = result.get("wsUrl") or result.get("debuggerAddress") or str(result)
            await callback.message.answer(  # type: ignore[union-attr]
                f"✅ GoLogin профиль <b>{token.name}</b> запущен.\n\n"
                f"WebSocket URL:\n<code>{ws_url}</code>",
                parse_mode="HTML",
            )
        except httpx.ConnectError:
            await callback.message.answer(  # type: ignore[union-attr]
                "❌ GoLogin Desktop не отвечает на localhost:36912.\n"
                "Убедись, что приложение GoLogin <b>открыто</b> на этом компьютере.",
                parse_mode="HTML",
            )
        except httpx.HTTPStatusError as e:
            logger.error("GoLogin API error: %s — %s", e.response.status_code, e.response.text)
            import html
            safe = html.escape(e.response.text[:300])
            await callback.message.answer(  # type: ignore[union-attr]
                f"❌ GoLogin ошибка {e.response.status_code}:\n<code>{safe}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("GoLogin launch error: %s", e)
            await callback.message.answer(f"❌ Ошибка: {e}")  # type: ignore[union-attr]
        return

    # Fallback — local Chrome with anti-detect
    await callback.answer("Запускаем локальный браузер…")
    try:
        ws_url = await BrowserService.launch(
            token.id,
            proxy=token.proxy or None,
            user_agent=token.user_agent or None,
        )
        proxy_line = f"\n🔒 Прокси: <code>{token.proxy}</code>" if token.proxy else "\n⚠️ Прокси не задан"
        await callback.message.answer(  # type: ignore[union-attr]
            f"✅ Локальный Chrome <b>{token.name}</b> запущен.{proxy_line}\n\n"
            f"WebSocket URL:\n<code>{ws_url}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Browser launch error for token %s: %s", token.id, e)
        await callback.message.answer(f"❌ Ошибка запуска браузера: {e}")  # type: ignore[union-attr]


@router.callback_query(F.data == "shift:release")
async def shift_release(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id  # type: ignore[union-attr]

    repo = TokenRepository(session)
    token = await repo.get_active_token(user_id)
    released = await repo.release_token(user_id)

    if released:
        if token:
            await BrowserService.stop(token.id)
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Токен освобождён. Хорошей работы!",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer("Токен освобождён.")
    else:
        await callback.answer("Активный токен не найден.", show_alert=True)
