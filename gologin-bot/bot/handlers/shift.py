import asyncio
import html
import logging

import httpx
from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.config import ADMIN_USERNAME, settings
from bot.db.repository import FolderRepository
from bot.keyboards.builder import (
    active_folder_keyboard,
    count_picker_keyboard,
    folder_info_keyboard,
    folder_list_keyboard,
    main_menu_keyboard,
)
from bot.services.gologin import GoLoginService
from bot.services.orchestrator import get_orchestrator

logger = logging.getLogger(__name__)
router = Router()


# ── No-op (display-only buttons) ──────────────────────────────────────────────

@router.callback_query(F.data == "shift:noop")
async def shift_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ── Token list ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "shift:folders")
async def shift_folders(callback: CallbackQuery, session: AsyncSession) -> None:
    repo = FolderRepository(session)
    folders = await repo.get_all_folders()

    if not folders:
        await callback.answer("Токены не найдены. Дождитесь синхронизации.", show_alert=True)
        return

    await callback.message.edit_text(  # type: ignore[union-attr]
        "Выбери токен:",
        reply_markup=folder_list_keyboard(folders),
    )
    await callback.answer()


# ── Free token selected → count picker ────────────────────────────────────────

@router.callback_query(F.data.startswith("shift:folder:"))
async def shift_select_folder(callback: CallbackQuery, session: AsyncSession) -> None:
    folder_id = int(callback.data.split(":")[-1])  # type: ignore[union-attr]

    repo = FolderRepository(session)
    folder = await repo.get_folder_by_id(folder_id)

    if not folder or not folder.is_free:
        await callback.answer("Токен уже занят, выбери другой.", show_alert=True)
        return

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"📁 <b>{folder.name}</b>\n\nСколько M-профилей запустить?",
        parse_mode="HTML",
        reply_markup=count_picker_keyboard(folder_id, n=1, max_n=folder.profile_count),
    )
    await callback.answer()


# ── Count picker navigation ────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("shift:count:"))
async def shift_count_navigate(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")  # type: ignore[union-attr]
    folder_id = int(parts[2])
    n = int(parts[3])

    repo = FolderRepository(session)
    folder = await repo.get_folder_by_id(folder_id)

    if not folder or not folder.is_free:
        await callback.answer("Токен уже занят.", show_alert=True)
        return

    n = max(1, min(n, folder.profile_count))

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"📁 <b>{folder.name}</b>\n\nСколько M-профилей запустить?",
        parse_mode="HTML",
        reply_markup=count_picker_keyboard(folder_id, n=n, max_n=folder.profile_count),
    )
    await callback.answer()


# ── Busy token → info page ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("shift:folder_info:"))
async def shift_folder_info(callback: CallbackQuery, session: AsyncSession) -> None:
    folder_id = int(callback.data.split(":")[-1])  # type: ignore[union-attr]

    repo = FolderRepository(session)
    folder = await repo.get_folder_by_id(folder_id)

    if not folder or folder.is_free:
        await callback.answer("Токен уже освобождён.", show_alert=True)
        return

    holder_name = "неизвестен"
    if folder.assigned_to:
        try:
            chat = await callback.bot.get_chat(folder.assigned_to)  # type: ignore[union-attr]
            parts = []
            if chat.first_name:
                parts.append(chat.first_name)
            if chat.last_name:
                parts.append(chat.last_name)
            holder_name = " ".join(parts) if parts else holder_name
            if chat.username:
                holder_name += f" (@{chat.username})"
        except Exception:
            holder_name = str(folder.assigned_to)

    since = ""
    if folder.assigned_at:
        since = f"\n🕐 С {folder.assigned_at.strftime('%d.%m %H:%M')} UTC"

    is_admin = callback.from_user.username == ADMIN_USERNAME  # type: ignore[union-attr]

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"🔒 <b>{folder.name}</b> занят\n\n"
        f"👤 {holder_name}{since}",
        parse_mode="HTML",
        reply_markup=folder_info_keyboard(folder_id, is_admin=is_admin),
    )
    await callback.answer()


# ── Admin force-release ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("shift:force_folder:"))
async def shift_force_release_folder(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.from_user.username != ADMIN_USERNAME:  # type: ignore[union-attr]
        await callback.answer("Нет прав.", show_alert=True)
        return

    folder_id = int(callback.data.split(":")[-1])  # type: ignore[union-attr]

    repo = FolderRepository(session)
    folder = await repo.get_folder_by_id(folder_id)
    folder_name = folder.name if folder else str(folder_id)

    released = await repo.force_release_folder(folder_id)
    if released:
        folders = await repo.get_all_folders()
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"✅ Токен <b>{folder_name}</b> принудительно освобождён.\n\nВыбери токен:",
            parse_mode="HTML",
            reply_markup=folder_list_keyboard(folders),
        )
        await callback.answer("Освобождено.")
    else:
        await callback.answer("Токен уже был свободен.", show_alert=True)


# ── Launch: TM + M1..MN → JWT extraction → agents ─────────────────────────────

@router.callback_query(F.data.startswith("shift:launch_folder:"))
async def shift_launch_folder(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")  # type: ignore[union-attr]
    folder_id = int(parts[2])
    count = int(parts[3])

    user_id = callback.from_user.id  # type: ignore[union-attr]

    repo = FolderRepository(session)
    folder = await repo.assign_folder(folder_id, user_id, count)

    if folder is None:
        folders = await repo.get_all_folders()
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Токен уже занят. Выбери другой:",
            reply_markup=folder_list_keyboard(folders),
        )
        await callback.answer("Токен уже занят!", show_alert=True)
        return

    if not folder.main_profile_id:
        await repo.release_folder(user_id)
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"⚠️ Токен <b>{folder.name}</b> не содержит ТМ профиля.\n\n"
            "Обратитесь к администратору.",
            parse_mode="HTML",
            reply_markup=active_folder_keyboard(),
        )
        return

    await callback.answer("Запускаем…")
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"⏳ Запускаем ТМ для <b>{folder.name}</b>…",
        parse_mode="HTML",
    )

    service = GoLoginService()
    tm_ws_url: str | None = None

    # Step 1 — Launch TM browser
    try:
        tm_result = await service.start_profile(folder.main_profile_id)
        tm_ws_url = tm_result.get("wsUrl") if isinstance(tm_result, dict) else None
    except httpx.ConnectError:
        await repo.release_folder(user_id)
        await callback.message.edit_text(  # type: ignore[union-attr]
            "❌ GoLogin Desktop не отвечает.\nУбедись что приложение открыто.",
            reply_markup=active_folder_keyboard(),
        )
        return
    except httpx.HTTPStatusError as e:
        await repo.release_folder(user_id)
        safe = html.escape(e.response.text[:200])
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"❌ Ошибка GoLogin {e.response.status_code}:\n<code>{safe}</code>",
            parse_mode="HTML",
            reply_markup=active_folder_keyboard(),
        )
        return
    except Exception as e:
        await repo.release_folder(user_id)
        logger.error("TM launch error: %s", e)
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"❌ Ошибка запуска ТМ: {html.escape(str(e))}",
            parse_mode="HTML",
            reply_markup=active_folder_keyboard(),
        )
        return

    # Step 2 — Check M-profiles exist
    numbered_ids = folder.numbered_ids[:count]
    if not numbered_ids:
        await repo.release_folder(user_id)
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"⚠️ Нет M-профилей в папке <b>{folder.name}</b>.\n\nОбратитесь к администратору.",
            parse_mode="HTML",
            reply_markup=active_folder_keyboard(),
        )
        return

    from bot.services.massmo_actions import extract_jwt, open_url_in_browser

    # Step 3 — Open dashboard in TM browser immediately
    dashboard_url = f"http://{settings.web_host}:{settings.web_port}"
    if tm_ws_url:
        asyncio.create_task(open_url_in_browser(tm_ws_url, dashboard_url))

    # Step 4 — Prepare orchestrator session
    orchestrator = get_orchestrator()
    orchestrator.set_notify(callback.bot, user_id)  # type: ignore[arg-type]
    await orchestrator.set_profile_map({f"M{i+1}": pid for i, pid in enumerate(folder.numbered_ids)})
    await orchestrator.begin_fresh_session()

    # Step 5 — Notify user and start sequential loading in background
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"✅ <b>{folder.name}</b>\n"
        f"Дашборд открывается в ТМ-браузере.\n"
        f"Профили M1–M{count} подгружаются последовательно…",
        parse_mode="HTML",
        reply_markup=active_folder_keyboard(),
    )

    async def _load_profiles_sequential() -> None:
        total = len(numbered_ids)
        for i, pid in enumerate(numbered_ids):
            label = f"M{i + 1}"
            await orchestrator.update_loading(i + 1, total, label)
            try:
                result = await service.start_profile(pid)
                ws_url = result.get("wsUrl") if isinstance(result, dict) else None
                if not ws_url:
                    logger.warning("No wsUrl for %s, skipping", label)
                    continue
                await asyncio.sleep(5)
                jwt = await extract_jwt(ws_url)
                await service.stop_profile(pid)
                if jwt:
                    await orchestrator.add_agent_jwt(label, jwt)
                    logger.info("Sequential: added agent %s", label)
                else:
                    logger.warning("Sequential: JWT extraction failed for %s", label)
            except Exception as exc:
                logger.error("Sequential load error for %s: %s", label, exc)
        await orchestrator.clear_loading()

    asyncio.create_task(_load_profiles_sequential())


# ── Release ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "shift:release")
async def shift_release(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id  # type: ignore[union-attr]

    repo = FolderRepository(session)

    # Check there is an active folder before stopping agents
    folder = await repo.get_active_folder(user_id)
    if not folder:
        await callback.answer("Активный токен не найден.", show_alert=True)
        return

    # Stop agents first so the next operator doesn't grab a busy folder
    await get_orchestrator().stop_agents()

    folder = await repo.release_folder(user_id)
    if not folder:
        await callback.answer("Активный токен не найден.", show_alert=True)
        return

    await callback.message.edit_text(  # type: ignore[union-attr]
        "Смена завершена. Хорошей работы!",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


