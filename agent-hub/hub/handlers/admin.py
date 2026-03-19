import base64
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from hub.core.config import ADMIN_USERNAME, settings
from hub.db.repository import AgentRepository, AgentSetupTokenRepository, FolderRepository, UserRepository

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


def _hub_public_url() -> str:
    if settings.hub_public_url:
        return settings.hub_public_url.rstrip("/")
    return f"http://{settings.hub_host}:{settings.hub_port}"


def _make_setup_token(hub_url: str, jti: str) -> str:
    payload = json.dumps({"hub_url": hub_url, "jti": jti}, separators=(",", ":"))
    b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"GLAGENT_{b64}"


@router.message(Command("register_agent"))
async def cmd_register_agent(message: Message, session: AsyncSession) -> None:
    """Usage: /register_agent <username_or_id> [agent_id_suffix]
    Example: /register_agent vasya
             /register_agent vasya mac-2
    """
    if not _admin_only(message):
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "<b>/register_agent</b> — создать токен установки агента\n\n"
            "Использование:\n"
            "  <code>/register_agent &lt;username&gt; [agent_id]</code>\n\n"
            "Примеры:\n"
            "  <code>/register_agent vasya</code>\n"
            "  <code>/register_agent vasya mac-2</code>",
            parse_mode="HTML",
        )
        return

    target = parts[1].lstrip("@")
    user_repo = UserRepository(session)

    # Resolve user: by numeric ID or by username
    user = None
    if target.isdigit():
        user = await user_repo.get_by_telegram_id(int(target))
    else:
        user = await user_repo.get_by_username(target)

    if not user:
        await message.answer(
            f"❌ Пользователь <code>{target}</code> не найден.\n"
            "Пользователь должен сначала написать /start боту.",
            parse_mode="HTML",
        )
        return

    # Determine agent_id
    username_slug = (user.username or str(user.telegram_id)).lower().replace(" ", "-")
    if len(parts) >= 3:
        agent_id = f"agent-{parts[2]}"
    else:
        agent_id = f"agent-{username_slug}"

    jti = secrets.token_hex(16)  # 32 hex chars = 128 bits entropy
    expires_at = datetime.utcnow() + timedelta(days=7)

    token_repo = AgentSetupTokenRepository(session)
    await token_repo.create(jti, agent_id, user.telegram_id, expires_at)

    hub_url = _hub_public_url()
    token_str = _make_setup_token(hub_url, jti)

    display_name = user.first_name or user.username or str(user.telegram_id)
    await message.answer(
        f"✅ Токен установки агента для <b>{display_name}</b>\n"
        f"Агент: <code>{agent_id}</code>\n"
        f"Действует 7 дней\n\n"
        f"<b>Linux / macOS:</b>\n"
        f"<pre>curl -fsSL https://raw.githubusercontent.com/luzenloh/universal-hub/main/agent-hub/install-agent.sh | bash -s -- {token_str}</pre>\n\n"
        f"<b>Windows (PowerShell):</b>\n"
        f"<pre>irm https://raw.githubusercontent.com/luzenloh/universal-hub/main/agent-hub/install-agent.ps1 | iex; Install-Agent '{token_str}'</pre>\n\n"
        f"Токен: <code>{token_str}</code>",
        parse_mode="HTML",
    )


@router.message(Command("revoke_agent"))
async def cmd_revoke_agent(message: Message, session: AsyncSession) -> None:
    """Usage: /revoke_agent <username_or_id>"""
    if not _admin_only(message):
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "Использование: <code>/revoke_agent &lt;username_or_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    target = parts[1].lstrip("@")
    user_repo = UserRepository(session)

    user = None
    if target.isdigit():
        user = await user_repo.get_by_telegram_id(int(target))
    else:
        user = await user_repo.get_by_username(target)

    if not user:
        await message.answer(f"❌ Пользователь <code>{target}</code> не найден.", parse_mode="HTML")
        return

    username_slug = (user.username or str(user.telegram_id)).lower().replace(" ", "-")
    agent_id = f"agent-{username_slug}"
    token_repo = AgentSetupTokenRepository(session)
    revoked = await token_repo.revoke_for_agent(agent_id)

    display_name = user.first_name or user.username or str(user.telegram_id)
    await message.answer(
        f"🚫 Отозвано токенов для <b>{display_name}</b> (агент <code>{agent_id}</code>): {revoked}",
        parse_mode="HTML",
    )


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
