import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.config import ADMIN_USERNAME
from bot.db.models import Token

logger = logging.getLogger(__name__)
router = Router()


def _admin_only(message: Message) -> bool:
    return message.from_user is not None and message.from_user.username == ADMIN_USERNAME


@router.message(Command("profiles"))
async def cmd_profiles(message: Message, session: AsyncSession) -> None:
    if not _admin_only(message):
        return

    result = await session.execute(select(Token).order_by(Token.id))
    tokens = list(result.scalars().all())

    if not tokens:
        await message.answer("Профили не найдены.")
        return

    lines = ["<b>Все профили:</b>\n"]
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
