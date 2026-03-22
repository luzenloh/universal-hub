from __future__ import annotations
"""Agent API — endpoints that Hub calls to control the Agent."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from web.models.schemas import AgentStartRequest, AgentStatus, WindowState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent")
_security = HTTPBearer()


def _verify_secret(request: Request, credentials: HTTPAuthorizationCredentials = Depends(_security)) -> None:
    hub_secret: str = request.app.state.hub_secret
    if not hub_secret or credentials.credentials != hub_secret:
        raise HTTPException(status_code=401, detail="Invalid hub secret")


@router.post("/start_shift", dependencies=[Depends(_verify_secret)])
async def start_shift(body: AgentStartRequest, request: Request) -> dict[str, str]:
    """Hub command: launch TM + M profiles, extract JWTs, create agents."""
    from bot.services.gologin import GoLoginService
    from bot.services.massmo_actions import extract_jwt, open_url_in_browser
    from bot.services.orchestrator import get_orchestrator
    from agent.core.config import settings as agent_settings

    orchestrator = get_orchestrator()

    await orchestrator.set_profile_map(
        {f"M{i+1}": pid for i, pid in enumerate(body.numbered_profile_ids)}
    )
    await orchestrator.begin_fresh_session()
    orchestrator.set_folder_name(body.folder_name)

    # Store inbound platform secrets if provided in extended format
    if isinstance(body.massmo_secrets, dict):
        orchestrator.set_shift_secrets(body.massmo_secrets)

    service = GoLoginService()

    async def _run() -> None:
        # Step 1 — Launch TM browser
        try:
            tm_result = await service.start_profile(body.main_profile_id)
            tm_ws_url = tm_result.get("wsUrl") if isinstance(tm_result, dict) else None
        except Exception as exc:
            logger.error("TM launch error: %s", exc)
            tm_ws_url = None

        # Step 2 — Open dashboard in TM browser
        dashboard_url = f"http://{agent_settings.agent_host}:{agent_settings.agent_port}"
        if tm_ws_url:
            asyncio.create_task(open_url_in_browser(tm_ws_url, dashboard_url))

        # Step 3 — Load M profiles sequentially
        numbered_ids = body.numbered_profile_ids[: body.count]
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

    asyncio.create_task(_run())
    return {"status": "started"}


@router.post("/stop_shift", dependencies=[Depends(_verify_secret)])
async def stop_shift(request: Request) -> dict[str, str]:
    """Hub command: stop all agents and logout."""
    from bot.services.orchestrator import get_orchestrator

    orchestrator = get_orchestrator()
    asyncio.create_task(orchestrator.stop_agents())
    return {"status": "stopped"}


@router.get("/status", response_model=AgentStatus, dependencies=[Depends(_verify_secret)])
async def get_status(request: Request) -> AgentStatus:
    """Hub query: get current agent session state."""
    from bot.services.orchestrator import get_orchestrator

    orchestrator = get_orchestrator()
    return AgentStatus(
        active=orchestrator.is_active(),
        windows=orchestrator.get_all_states(),
    )
