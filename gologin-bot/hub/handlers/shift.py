"""Hub shift handlers — folder selection UI → delegate launch to Agent via REST."""
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from hub.core.config import ADMIN_USERNAME
from hub.db.repository import AgentRepository, FolderRepository
from hub.keyboards.builder import (
    active_folder_keyboard,
    count_picker_keyboard,
    folder_info_keyboard,
    folder_list_keyboard,
    main_menu_keyboard,
)
from hub.services import agent_client
from web.models.schemas import AgentStartRequest

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "shift:noop")
async def shift_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "shift:folders")
async def shift_folders(callback: CallbackQuery, session: AsyncSession) -> None:
    repo = FolderRepository(session)
    folders = await repo.get_all_folders()

    if not folders:
        await callback.answer("Папки не найдены. Дождитесь синхронизации.", show_alert=True)
        return

    await callback.message.edit_text(  # type: ignore[union-attr]
        "Выбери токен:",
        reply_markup=folder_list_keyboard(folders),
    )
    await callback.answer()


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
        f"🔒 <b>{folder.name}</b> занят\n\n👤 {holder_name}{since}",
        parse_mode="HTML",
        reply_markup=folder_info_keyboard(folder_id, is_admin=is_admin),
    )
    await callback.answer()


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


@router.callback_query(F.data.startswith("shift:launch_folder:"))
async def shift_launch_folder(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")  # type: ignore[union-attr]
    folder_id = int(parts[2])
    count = int(parts[3])
    user_id = callback.from_user.id  # type: ignore[union-attr]

    folder_repo = FolderRepository(session)
    agent_repo = AgentRepository(session)

    # Find the agent that belongs to this Telegram user
    agent = await agent_repo.get_agent_by_owner(user_id)
    if not agent:
        await callback.answer(
            "Твой агент не найден или занят. Убедись что agent_main.py запущен и OWNER_TELEGRAM_ID указан верно.",
            show_alert=True,
        )
        return

    # Atomically assign folder
    folder = await folder_repo.assign_folder(folder_id, user_id, count, agent.agent_id)
    if folder is None:
        folders = await folder_repo.get_all_folders()
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Токен уже занят. Выбери другой:",
            reply_markup=folder_list_keyboard(folders),
        )
        await callback.answer("Токен уже занят!", show_alert=True)
        return

    if not folder.main_profile_id:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"⚠️ Токен <b>{folder.name}</b> не содержит ТМ профиля.\n\n"
            "Обратитесь к администратору.",
            parse_mode="HTML",
            reply_markup=active_folder_keyboard(),
        )
        return

    # Pass ALL profile IDs so agent builds a full profile_map (M1–M15).
    # count controls how many are actually launched; the rest stay available
    # for manual add via the dashboard "+ профиль" button.
    numbered_ids = folder.numbered_ids
    massmo_secrets = folder.massmo_secrets_list

    if not numbered_ids:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"⚠️ Нет M-профилей в папке <b>{folder.name}</b>.\n\nОбратитесь к администратору.",
            parse_mode="HTML",
            reply_markup=active_folder_keyboard(),
        )
        return

    # Link agent to folder
    await agent_repo.assign_agent_to_folder(agent.agent_id, folder_id, user_id)

    await callback.answer("Запускаем…")
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"⏳ Отправляем команду агенту для <b>{folder.name}</b>…",
        parse_mode="HTML",
    )

    payload = AgentStartRequest(
        folder_gologin_id=folder.gologin_id,
        folder_name=folder.name,
        main_profile_id=folder.main_profile_id,
        numbered_profile_ids=numbered_ids,
        massmo_secrets=massmo_secrets,
        count=count,
        notify_chat_id=user_id,
    )

    ok = await agent_client.start_shift(agent, payload)
    if ok:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"✅ <b>{folder.name}</b>\n"
            f"Агент запускает профили M1–M{count}…\n"
            f"Дашборд откроется в ТМ-браузере.",
            parse_mode="HTML",
            reply_markup=active_folder_keyboard(),
        )
    else:
        # Rollback
        await folder_repo.force_release_folder(folder_id)
        await agent_repo.release_agent(agent.agent_id)
        await callback.message.edit_text(  # type: ignore[union-attr]
            "❌ Агент не ответил. Проверь что agent_main.py запущен.",
            reply_markup=main_menu_keyboard(),
        )


@router.callback_query(F.data == "shift:release")
async def shift_release(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id  # type: ignore[union-attr]

    folder_repo = FolderRepository(session)
    folder = await folder_repo.get_active_folder(user_id)

    if not folder:
        await callback.answer("Активный токен не найден.", show_alert=True)
        return

    agent_repo = AgentRepository(session)
    agent_id = folder.assigned_agent_id

    # Release DB records first (best-effort stop on agent)
    await folder_repo.release_folder(user_id)

    if agent_id:
        agent = await agent_repo.get_agent_by_id(agent_id)
        if agent:
            await agent_client.stop_shift(agent)
        await agent_repo.release_agent(agent_id)

    await callback.message.edit_text(  # type: ignore[union-attr]
        "Смена завершена. Хорошей работы!",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()
