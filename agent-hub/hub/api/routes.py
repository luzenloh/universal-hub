"""Hub API — Agent registration and heartbeat endpoint."""
import asyncio
import logging
import time

from aiogram.exceptions import TelegramBadRequest
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hub.core.config import settings
from hub.db.base import async_session_factory
from hub.db.repository import AgentRepository, AgentSetupTokenRepository
from web.models.schemas import HeartbeatPayload, RegisterPayload, WindowStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hub")
_security = HTTPBearer()

# Throttle: agent_id → last pin-edit timestamp
_last_pin_update: dict[str, float] = {}


def _verify_secret(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> None:
    if credentials.credentials != settings.hub_secret:
        raise HTTPException(status_code=401, detail="Invalid hub secret")


async def _delete_after(bot, chat_id: int, msg_id: int, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


def _build_pin_summary(windows: list) -> str:
    """Build summary text for pinned message from heartbeat window states."""
    STATUS_EMOJI = {
        WindowStatus.IDLE: "🔵",
        WindowStatus.SEARCHING: "🟡",
        WindowStatus.ACTIVE_PAYOUT: "🟠",
        WindowStatus.EXPIRING: "🔴",
        WindowStatus.VERIFICATION: "🔍",
        WindowStatus.VERIFICATION_FAILED: "❌",
        WindowStatus.PAID: "✅",
        WindowStatus.DISABLED: "🟣",
        WindowStatus.ERROR: "⚠️",
        WindowStatus.CONNECTING: "⚪",
        WindowStatus.STOPPED: "⛔",
    }
    paid_count = sum(1 for w in windows if w.status == WindowStatus.PAID)
    active_count = sum(1 for w in windows if w.status == WindowStatus.ACTIVE_PAYOUT)
    lines = []
    for w in sorted(windows, key=lambda x: x.label):
        emoji = STATUS_EMOJI.get(w.status, "❓")
        detail = ""
        if w.payout and w.payout.amount and w.status in (
            WindowStatus.ACTIVE_PAYOUT, WindowStatus.EXPIRING, WindowStatus.VERIFICATION
        ):
            detail = f" {w.payout.amount}"
        lines.append(f"{emoji} {w.label}{detail}")

    now_str = time.strftime("%H:%M:%S")
    header = f"📊 Смена активна | ✅ {paid_count} выплат | 🟠 {active_count} активных\n"
    body = "  ".join(lines)
    return f"{header}{body}\n\nОбновлено: {now_str}"


@router.post("/register", dependencies=[Depends(_verify_secret)])
async def hub_register(body: RegisterPayload) -> dict[str, str]:
    """Agent calls this on startup to announce itself."""
    async with async_session_factory() as session:
        repo = AgentRepository(session)
        await repo.upsert_agent(body.agent_id, body.public_url, body.local_url, body.owner_telegram_id)
    logger.info("Agent registered: %s local=%s public=%s", body.agent_id, body.local_url, body.public_url)
    return {"status": "registered"}


@router.post("/heartbeat", dependencies=[Depends(_verify_secret)])
async def hub_heartbeat(body: HeartbeatPayload, request: Request) -> dict[str, str]:
    """Agent sends heartbeat every 10s with current window states."""
    bot = request.app.state.bot
    prev_states: dict[str, dict[str, str]] = request.app.state.agent_prev_states

    # Read prev BEFORE updating so new_paid diff is correct
    prev = prev_states.get(body.agent_id, {})

    async with async_session_factory() as session:
        repo = AgentRepository(session)
        await repo.update_heartbeat(body.agent_id)
        agent = await repo.get_agent_by_id(body.agent_id)

    notify_chat_id = agent.notify_chat_id if agent else None
    pinned_message_id = agent.pinned_message_id if agent else None
    pinned_chat_id = agent.pinned_chat_id if agent else None

    # Diff states — send ephemeral Telegram notifications (reduced set)
    for ws in body.windows:
        prev_status = prev.get(ws.window_id)
        if prev_status == ws.status:
            continue

        text: str | None = None
        auto_delete: float | None = None

        if ws.status == WindowStatus.EXPIRING and prev_status != WindowStatus.EXPIRING:
            p = ws.payout
            amount = p.amount or "" if p else ""
            text = f"⏰ {ws.label}: срок выплаты истекает! {amount}"
            auto_delete = 60.0
        elif ws.status == WindowStatus.VERIFICATION_FAILED and prev_status != WindowStatus.VERIFICATION_FAILED:
            text = f"❌ {ws.label}: платёж не прошёл проверку"
        elif ws.status == WindowStatus.ERROR and prev_status != WindowStatus.ERROR:
            text = f"⚠️ {ws.label}: ошибка — {ws.error_msg or 'неизвестно'}"

        if text and notify_chat_id and bot:
            try:
                sent = await bot.send_message(notify_chat_id, text)
                if auto_delete:
                    asyncio.create_task(_delete_after(bot, notify_chat_id, sent.message_id, auto_delete))
            except Exception as exc:
                logger.warning("Telegram notify failed: %s", exc)

    # Update prev states for this agent
    prev_states[body.agent_id] = {ws.window_id: ws.status for ws in body.windows}

    # Update pinned message (throttle: max once per 5s per agent)
    if pinned_message_id and pinned_chat_id and bot:
        now = time.time()
        if now - _last_pin_update.get(body.agent_id, 0) >= 5.0:
            summary = _build_pin_summary(body.windows)
            try:
                await bot.edit_message_text(
                    chat_id=pinned_chat_id,
                    message_id=pinned_message_id,
                    text=summary,
                )
                _last_pin_update[body.agent_id] = now
            except TelegramBadRequest as exc:
                err = str(exc)
                if "message is not modified" in err:
                    _last_pin_update[body.agent_id] = now
                elif "message to edit not found" in err:
                    async with async_session_factory() as session:
                        await AgentRepository(session).clear_pinned_message(body.agent_id)
                    _last_pin_update.pop(body.agent_id, None)
            except Exception as exc:
                logger.warning("Pin message update failed: %s", exc)

    # Update agent statistics
    active_count = sum(1 for w in body.windows if w.status == WindowStatus.ACTIVE_PAYOUT)
    searching_count = sum(1 for w in body.windows if w.status == WindowStatus.SEARCHING)
    new_paid = sum(
        1 for w in body.windows
        if w.status == WindowStatus.PAID and prev.get(w.window_id) != WindowStatus.PAID
    )
    async with async_session_factory() as session:
        await AgentRepository(session).update_agent_stats(
            body.agent_id, active_count, searching_count, new_paid
        )

    return {"status": "ok"}


@router.get("/claim/{jti}")
async def hub_claim(jti: str) -> dict:
    """One-time endpoint called by the agent installer to claim its config.

    The jti is a 32-char random hex embedded in the GLAGENT_* setup token.
    No Bearer auth — the jti itself is the shared secret (128-bit entropy, single-use).
    """
    async with async_session_factory() as session:
        token_repo = AgentSetupTokenRepository(session)
        token = await token_repo.get_valid(jti)
        if not token:
            raise HTTPException(status_code=404, detail="Token not found, expired, or already used")
        await token_repo.mark_used(jti)
        # Pre-create agent record so heartbeats land even before tunnel is up
        agent_repo = AgentRepository(session)
        await agent_repo.upsert_agent(
            token.agent_id, public_url="", local_url="",
            owner_telegram_id=token.owner_telegram_id,
        )

    logger.info("Setup token claimed: agent_id=%s owner=%s", token.agent_id, token.owner_telegram_id)
    return {
        "hub_url": settings.hub_public_url or f"http://{settings.hub_host}:{settings.hub_port}",
        "hub_secret": settings.hub_secret,
        "agent_id": token.agent_id,
        "owner_telegram_id": token.owner_telegram_id,
        "agent_port": 8081,
    }


@router.get("/agents", dependencies=[Depends(_verify_secret)])
async def hub_agents() -> list[dict]:
    """Return all registered agents (admin endpoint)."""
    async with async_session_factory() as session:
        repo = AgentRepository(session)
        agents = await repo.get_all_agents()

    return [
        {
            "agent_id": a.agent_id,
            "local_url": a.local_url,
            "public_url": a.public_url,
            "is_active": a.is_active,
            "assigned_folder_id": a.assigned_folder_id,
            "last_seen": a.last_seen.isoformat() if a.last_seen else None,
        }
        for a in agents
    ]
