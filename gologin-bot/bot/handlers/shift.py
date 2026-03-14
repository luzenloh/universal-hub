import html
import logging

import httpx
from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.config import ADMIN_USERNAME
from bot.db.repository import FolderRepository
from bot.keyboards.builder import (
    active_folder_keyboard,
    folder_info_keyboard,
    folder_list_keyboard,
    main_menu_keyboard,
    profile_count_keyboard,
)
from bot.services.gologin import GoLoginService

logger = logging.getLogger(__name__)
router = Router()


# ── Folder list ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "shift:folders")
async def shift_folders(callback: CallbackQuery, session: AsyncSession) -> None:
    repo = FolderRepository(session)
    folders = await repo.get_all_folders()

    if not folders:
        await callback.answer("Папки не найдены. Дождитесь синхронизации.", show_alert=True)
        return

    await callback.message.edit_text(  # type: ignore[union-attr]
        "Выбери папку GoLogin:",
        reply_markup=folder_list_keyboard(folders),
    )
    await callback.answer()


# ── Free folder selected → count picker ───────────────────────────────────────

@router.callback_query(F.data.startswith("shift:folder:"))
async def shift_select_folder(callback: CallbackQuery, session: AsyncSession) -> None:
    folder_id = int(callback.data.split(":")[-1])  # type: ignore[union-attr]

    repo = FolderRepository(session)
    folder = await repo.get_folder_by_id(folder_id)

    if not folder or not folder.is_free:
        await callback.answer("Папка уже занята, выбери другую.", show_alert=True)
        return

    max_count = folder.profile_count  # numbered profiles only
    has_main = bool(folder.main_profile_id)
    main_label = " + ТМ глав" if has_main else ""

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"📁 <b>{folder.name}</b>\n\n"
        f"Сколько профилей запустить{main_label}?\n"
        f"(M1…MN{main_label})",
        parse_mode="HTML",
        reply_markup=profile_count_keyboard(folder_id, max_count),
    )
    await callback.answer()


# ── Busy folder selected → info page ──────────────────────────────────────────

@router.callback_query(F.data.startswith("shift:folder_info:"))
async def shift_folder_info(callback: CallbackQuery, session: AsyncSession) -> None:
    folder_id = int(callback.data.split(":")[-1])  # type: ignore[union-attr]

    repo = FolderRepository(session)
    folder = await repo.get_folder_by_id(folder_id)

    if not folder or folder.is_free:
        await callback.answer("Папка уже освобождена.", show_alert=True)
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
        since = f"\n🕐 Начало сессии: {folder.assigned_at.strftime('%d.%m.%Y %H:%M')} UTC"

    count_info = ""
    if folder.selected_count is not None:
        count_info = f"\n🖥 Запущено профилей: M1…M{folder.selected_count}"
        if folder.main_profile_id:
            count_info += " + ТМ глав"

    is_admin = callback.from_user.username == ADMIN_USERNAME  # type: ignore[union-attr]

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"🔒 Папка <b>{folder.name}</b> занята\n\n"
        f"👤 Кто занял: {holder_name}"
        f"{since}{count_info}",
        parse_mode="HTML",
        reply_markup=folder_info_keyboard(folder_id, is_admin=is_admin),
    )
    await callback.answer()


# ── Admin force-release folder ─────────────────────────────────────────────────

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
            f"✅ Папка <b>{folder_name}</b> принудительно освобождена.\n\nВыбери папку GoLogin:",
            parse_mode="HTML",
            reply_markup=folder_list_keyboard(folders),
        )
        await callback.answer("Освобождено.")
    else:
        await callback.answer("Папка уже была свободна.", show_alert=True)


# ── Launch: assign folder + start profiles ─────────────────────────────────────

@router.callback_query(F.data.startswith("shift:launch_folder:"))
async def shift_launch_folder(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")  # type: ignore[union-attr]
    folder_id = int(parts[2])
    count = int(parts[3])

    user_id = callback.from_user.id  # type: ignore[union-attr]

    repo = FolderRepository(session)
    folder = await repo.assign_folder(folder_id, user_id, count)

    if folder is None:
        # Race condition — folder was taken between list render and click
        folders = await repo.get_all_folders()
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Папка уже занята. Выбери другую:",
            reply_markup=folder_list_keyboard(folders),
        )
        await callback.answer("Папка уже занята!", show_alert=True)
        return

    await callback.answer("Запускаем профили…")

    # Build list of profile IDs to start
    numbered = folder.numbered_ids[:count]  # M1..Mcount
    all_profile_ids: list[str] = []
    if folder.main_profile_id:
        all_profile_ids.append(folder.main_profile_id)
    all_profile_ids.extend(numbered)

    if not all_profile_ids:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"⚠️ Папка <b>{folder.name}</b> не содержит профилей.\n\n"
            "Обратитесь к администратору для синхронизации.",
            parse_mode="HTML",
            reply_markup=active_folder_keyboard(),
        )
        return

    service = GoLoginService()

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"⏳ Запускаем {len(all_profile_ids)} профилей для <b>{folder.name}</b>…",
        parse_mode="HTML",
    )

    results = await service.start_profiles(all_profile_ids)

    # Build result message
    lines: list[str] = [f"📁 <b>{folder.name}</b> — запущено {len(all_profile_ids)} профилей\n"]
    m_counter = 1
    for pid, res in zip(all_profile_ids, results):
        if pid == folder.main_profile_id:
            label = "ТМ глав"
        else:
            label = f"M{m_counter}"
            m_counter += 1

        if "error" in res:
            err = html.escape(str(res["error"])[:100])
            lines.append(f"❌ <b>{label}</b>: {err}")
        else:
            ws = res.get("wsUrl") or res.get("debuggerAddress") or "запущен"
            lines.append(f"✅ <b>{label}</b>: <code>{ws}</code>")

    await callback.message.answer(  # type: ignore[union-attr]
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=active_folder_keyboard(),
    )


# ── Release folder ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "shift:release")
async def shift_release(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id  # type: ignore[union-attr]

    repo = FolderRepository(session)
    folder = await repo.release_folder(user_id)

    if not folder:
        await callback.answer("Активная папка не найдена.", show_alert=True)
        return

    # Stop all running profiles in background (best-effort)
    all_ids: list[str] = []
    if folder.main_profile_id and folder.selected_count is not None:
        all_ids.append(folder.main_profile_id)
    if folder.selected_count is not None:
        all_ids.extend(folder.numbered_ids[: folder.selected_count])

    if all_ids:
        service = GoLoginService()
        await service.stop_profiles(all_ids)

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"✅ Папка <b>{folder.name}</b> освобождена. Хорошей работы!",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer("Папка освобождена.")
