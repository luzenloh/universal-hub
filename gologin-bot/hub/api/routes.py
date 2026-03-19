"""Hub API — Agent registration and heartbeat endpoint."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hub.core.config import settings
from hub.db.base import async_session_factory
from hub.db.repository import AgentRepository
from web.models.schemas import HeartbeatPayload, RegisterPayload, WindowStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hub")
_security = HTTPBearer()


def _verify_secret(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> None:
    if credentials.credentials != settings.hub_secret:
        raise HTTPException(status_code=401, detail="Invalid hub secret")


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

    async with async_session_factory() as session:
        repo = AgentRepository(session)
        await repo.update_heartbeat(body.agent_id)
        agent = await repo.get_agent_by_id(body.agent_id)

    notify_chat_id = agent.notify_chat_id if agent else None
    prev = prev_states.get(body.agent_id, {})

    # Diff states and send Telegram notifications
    for ws in body.windows:
        prev_status = prev.get(ws.window_id)
        if prev_status == ws.status:
            continue

        text: str | None = None
        if ws.status == WindowStatus.ACTIVE_PAYOUT and prev_status != WindowStatus.ACTIVE_PAYOUT:
            p = ws.payout
            if p:
                amount = p.amount or ""
                bank = p.bank or ""
                recipient = p.recipient or ""
                timer = p.timer or ""
                text = f"💰 {ws.label}: {amount} • {bank}\n{recipient}"
                if timer:
                    text += f"\n⏰ {timer}"
            else:
                text = f"💰 {ws.label}: активная выплата"
        elif ws.status == WindowStatus.VERIFICATION and prev_status != WindowStatus.VERIFICATION:
            text = f"🔍 {ws.label}: чек на проверке"
        elif ws.status == WindowStatus.VERIFICATION_FAILED and prev_status != WindowStatus.VERIFICATION_FAILED:
            text = f"❌ {ws.label}: платёж не прошёл проверку"
        elif ws.status == WindowStatus.PAID and prev_status != WindowStatus.PAID:
            text = f"✅ {ws.label}: выплата подтверждена"
        elif ws.status == WindowStatus.ERROR and prev_status != WindowStatus.ERROR:
            text = f"⚠️ {ws.label}: ошибка — {ws.error_msg or 'неизвестно'}"

        if text and notify_chat_id and bot:
            try:
                await bot.send_message(notify_chat_id, text)
            except Exception as exc:
                logger.warning("Telegram notify failed: %s", exc)

    # Update prev states for this agent
    prev_states[body.agent_id] = {ws.window_id: ws.status for ws in body.windows}

    return {"status": "ok"}


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
