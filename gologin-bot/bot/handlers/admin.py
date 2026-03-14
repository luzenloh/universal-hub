import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.config import ADMIN_USERNAME
from bot.db.models import Folder, Token
from bot.db.repository import FolderRepository

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
            lines.append(f"✅ <b>{f.name}</b>")
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
            lines.append(f"🔒 <b>{f.name}</b>\n  👤 {holder_name}")

    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router.message(Command("sync"))
async def cmd_sync(message: Message) -> None:
    if not _admin_only(message):
        return

    await message.answer("⏳ Синхронизация папок GoLogin…")
    try:
        from bot.db.base import async_session_factory
        from bot.services.sync import sync_folders
        await sync_folders(async_session_factory)
        await message.answer("✅ Синхронизация завершена.")
    except Exception as e:
        logger.error("Sync error: %s", e)
        await message.answer(f"❌ Ошибка синхронизации: {e}")


@router.message(Command("profiles"))
async def cmd_profiles(message: Message, session: AsyncSession) -> None:
    if not _admin_only(message):
        return

    result = await session.execute(select(Token).order_by(Token.id))
    tokens = list(result.scalars().all())

    if not tokens:
        await message.answer("Профили не найдены.")
        return

    lines = ["<b>Все токены:</b>\n"]
    for t in tokens:
        status = "✅ свободен" if t.is_free else "🔒 занят"
        proxy = f"<code>{t.proxy}</code>" if t.proxy else "—"
        ua = f"<code>{t.user_agent[:40]}…</code>" if t.user_agent and len(t.user_agent) > 40 else (f"<code>{t.user_agent}</code>" if t.user_agent else "—")
        lines.append(f"<b>{t.name}</b> [{status}]\n  Прокси: {proxy}\n  UA: {ua}")

    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router.message(Command("setproxy"))
async def cmd_setproxy(message: Message, session: AsyncSession) -> None:
    """Usage: /setproxy М1 http://user:pass@host:port"""
    if not _admin_only(message):
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Использование: /setproxy &lt;имя&gt; &lt;proxy_url&gt;\nПример: /setproxy М1 http://user:pass@1.2.3.4:8080", parse_mode="HTML")
        return

    name, proxy_url = parts[1], parts[2]
    result = await session.execute(
        update(Token).where(Token.name == name).values(proxy=proxy_url).returning(Token.id)
    )
    await session.commit()

    if result.fetchone():
        await message.answer(f"✅ Прокси для <b>{name}</b> установлен:\n<code>{proxy_url}</code>", parse_mode="HTML")
    else:
        await message.answer(f"❌ Профиль <b>{name}</b> не найден.", parse_mode="HTML")


@router.message(Command("clearproxy"))
async def cmd_clearproxy(message: Message, session: AsyncSession) -> None:
    """Usage: /clearproxy М1"""
    if not _admin_only(message):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /clearproxy &lt;имя&gt;", parse_mode="HTML")
        return

    name = parts[1]
    result = await session.execute(
        update(Token).where(Token.name == name).values(proxy=None).returning(Token.id)
    )
    await session.commit()

    if result.fetchone():
        await message.answer(f"✅ Прокси для <b>{name}</b> удалён.", parse_mode="HTML")
    else:
        await message.answer(f"❌ Профиль <b>{name}</b> не найден.", parse_mode="HTML")


@router.message(Command("setua"))
async def cmd_setua(message: Message, session: AsyncSession) -> None:
    """Usage: /setua М1 Mozilla/5.0 (Windows NT 10.0; Win64; x64) ..."""
    if not _admin_only(message):
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Использование: /setua &lt;имя&gt; &lt;user_agent&gt;", parse_mode="HTML")
        return

    name, ua = parts[1], parts[2]
    result = await session.execute(
        update(Token).where(Token.name == name).values(user_agent=ua).returning(Token.id)
    )
    await session.commit()

    if result.fetchone():
        await message.answer(f"✅ User-Agent для <b>{name}</b> установлен.", parse_mode="HTML")
    else:
        await message.answer(f"❌ Профиль <b>{name}</b> не найден.", parse_mode="HTML")
