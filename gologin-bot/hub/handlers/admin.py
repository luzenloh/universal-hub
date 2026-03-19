import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from hub.core.config import ADMIN_USERNAME
from hub.db.repository import AgentRepository, FolderRepository

logger = logging.getLogger(__name__)
router = Router()


def _admin_only(message: Message) -> bool:
    return message.from_user is not None and message.from_user.username == ADMIN_USERNAME


@router.message(Command("folders"))
async def cmd_folders(message: Message, session: AsyncSession) -> None:
    if not _admin_only(message):
        return

    repo = FolderRepository(session)
    folders = await repo.get_all_folders()

    if not folders:
        await message.answer("Папки не найдены.")
        return

    lines = ["<b>Папки GoLogin:</b>\n"]
    for f in folders:
        if f.is_free:
            lines.append(f"✅ <b>{f.name}</b> [id={f.id}]")
        else:
            holder_name = str(f.assigned_to)
            if f.assigned_to:
                try:
                    chat = await message.bot.get_chat(f.assigned_to)  # type: ignore[union-attr]
                    parts = []
                    if chat.first_name:
                        parts.append(chat.first_name)
                    if chat.last_name:
                        parts.append(chat.last_name)
                    holder_name = " ".join(parts) if parts else holder_name
                    if chat.username:
                        holder_name += f" (@{chat.username})"
                except Exception:
                    pass
            agent_info = f" | агент: {f.assigned_agent_id}" if f.assigned_agent_id else ""
            lines.append(f"🔒 <b>{f.name}</b> [id={f.id}]\n  👤 {holder_name}{agent_info}")

    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router.message(Command("sync"))
async def cmd_sync(message: Message) -> None:
    if not _admin_only(message):
        return

    await message.answer("⏳ Синхронизация папок GoLogin…")
    try:
        from hub.db.base import async_session_factory
        from hub.services.sync import sync_folders
        await sync_folders(async_session_factory)
        await message.answer("✅ Синхронизация завершена.")
    except Exception as e:
        logger.error("Sync error: %s", e)
        await message.answer(f"❌ Ошибка синхронизации: {e}")


@router.message(Command("agents"))
async def cmd_agents(message: Message, session: AsyncSession) -> None:
    if not _admin_only(message):
        return

    repo = AgentRepository(session)
    agents = await repo.get_all_agents()

    if not agents:
        await message.answer("Нет зарегистрированных агентов.")
        return

    lines = ["<b>Агенты:</b>\n"]
    for a in agents:
        status = "🟢 активен" if a.is_active else "🔴 неактивен"
        folder = f"папка id={a.assigned_folder_id}" if a.assigned_folder_id else "свободен"
        seen = a.last_seen.strftime("%d.%m %H:%M") if a.last_seen else "—"
        lines.append(
            f"{status} <b>{a.agent_id}</b>\n"
            f"  {folder} | seen: {seen}\n"
            f"  local: <code>{a.local_url}</code>"
        )

    await message.answer("\n\n".join(lines), parse_mode="HTML")


def _relative_time(dt: datetime | None) -> str:
    if not dt:
        return "—"
    now = datetime.utcnow()
    diff = int((now - dt).total_seconds())
    if diff < 60:
        return f"{diff}с назад"
    if diff < 3600:
        return f"{diff // 60}м назад"
    return f"{diff // 3600}ч назад"


async def _build_team_text(session: AsyncSession) -> str:
    repo = AgentRepository(session)
    agents = await repo.get_all_agents()
    if not agents:
        return "Нет зарегистрированных агентов."

    lines = ["<b>Команда — статус агентов:</b>\n"]
    for a in agents:
        status = "🟢" if a.is_active else "🔴"
        active = getattr(a, "active_payout_count", 0) or 0
        searching = getattr(a, "searching_count", 0) or 0
        paid = getattr(a, "session_payout_count", 0) or 0
        last_pay = _relative_time(getattr(a, "last_payout_at", None))
        folder = f"папка id={a.assigned_folder_id}" if a.assigned_folder_id else "свободен"
        lines.append(
            f"{status} <b>{a.agent_id}</b> | {folder}\n"
            f"  🟠 Выплат: {active} активных | 🟡 {searching} поиск | ✅ {paid} за смену\n"
            f"  Последняя выплата: {last_pay}"
        )
    return "\n\n".join(lines)


@router.message(Command("team"))
async def cmd_team(message: Message, session: AsyncSession) -> None:
    if not _admin_only(message):
        return

    text = await _build_team_text(session)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="admin:team")
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data == "admin:team")
async def cb_team(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.from_user.username != ADMIN_USERNAME:  # type: ignore[union-attr]
        await callback.answer("Нет прав.", show_alert=True)
        return
    text = await _build_team_text(session)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="admin:team")
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())  # type: ignore[union-attr]
    except Exception:
        pass
    await callback.answer()


@router.message(Command("set_secrets"))
async def cmd_set_secrets(message: Message, session: AsyncSession) -> None:
    """Usage: /set_secrets <folder_id> <secret1> <secret2> ..."""
    if not _admin_only(message):
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(
            "Использование: /set_secrets &lt;folder_id&gt; &lt;secret1&gt; &lt;secret2&gt; ...\n\n"
            "folder_id можно узнать командой /folders",
            parse_mode="HTML",
        )
        return

    try:
        folder_id = int(parts[1])
    except ValueError:
        await message.answer("❌ folder_id должен быть числом.")
        return

    secrets = parts[2:]
    repo = FolderRepository(session)
    ok = await repo.set_massmo_secrets(folder_id, secrets)
    if ok:
        await message.answer(
            f"✅ Сохранено <b>{len(secrets)}</b> секретов для папки <b>{folder_id}</b>.",
            parse_mode="HTML",
        )
    else:
        await message.answer(f"❌ Папка с id={folder_id} не найдена.")
