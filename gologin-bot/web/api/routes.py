from __future__ import annotations
import logging
import os
import tempfile

import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile

from bot.services.orchestrator import Orchestrator
from web.models.schemas import (
    CommandRequest,
    CommandResult,
    CommandType,
    ConnectRequest,
    WindowState,
)
from pydantic import BaseModel

class AddProfileRequest(BaseModel):
    label: str

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


def _orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/loading")
async def get_loading(request: Request) -> dict:
    return _orchestrator(request).get_loading_progress() or {}


@router.get("/banks")
async def get_banks(request: Request) -> list[dict]:
    """Proxy GET /banks from MassMO API using any active agent JWT."""
    orch = _orchestrator(request)
    jwt: str | None = None
    for agent in orch._agents.values():
        jwt = agent.get_jwt()
        if jwt:
            break
    if not jwt:
        raise HTTPException(status_code=503, detail="No active agents to proxy bank list")

    _MASSMO_API = "https://findssnet.io/api/massmo/v1"
    _HEADERS = {
        "Authorization": f"Bearer {jwt}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://massmo.io",
        "Referer": "https://massmo.io/",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"{_MASSMO_API}/banks", headers=_HEADERS, params={"per_page": 200})
            r.raise_for_status()
            data = r.json().get("data") or []
            return [{"alias": b["alias"], "name": b["name"], "logo": b.get("logo", "")} for b in data]
    except Exception as exc:
        logger.warning("Failed to fetch bank list: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/windows", response_model=list[WindowState])
async def get_windows(request: Request) -> list[WindowState]:
    return _orchestrator(request).get_all_states()


@router.get("/windows/available")
async def get_available_labels(request: Request) -> dict:
    return {"labels": _orchestrator(request).get_available_labels()}


@router.post("/windows/add", response_model=WindowState)
async def add_profile_by_label(body: AddProfileRequest, request: Request) -> WindowState:
    orch = _orchestrator(request)
    try:
        return await orch.add_profile_by_label(body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("add_profile_by_label failed")
        raise HTTPException(status_code=500, detail=str(exc))



@router.delete("/windows/{window_id}")
async def remove_window(window_id: str, request: Request) -> dict[str, str]:
    """Stop and remove a single agent."""
    await _orchestrator(request).remove_agent(window_id)
    return {"status": "removed"}


@router.post("/session/connect", response_model=list[WindowState])
async def connect_session(body: ConnectRequest, request: Request) -> list[WindowState]:
    """Connect one or more profiles by MassMO secret (adds without stopping others)."""
    orch = _orchestrator(request)
    states: list[WindowState] = []
    try:
        for e in body.profiles:
            state = await orch.add_profile(e.label, e.label, e.secret)
            states.append(state)
        return states
    except Exception as exc:
        logger.exception("connect_session failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/session/stop")
async def stop_session(request: Request) -> dict[str, str]:
    await _orchestrator(request).stop_session()
    return {"status": "stopped"}


@router.post("/windows/{window_id}/command", response_model=CommandResult)
async def send_command(window_id: str, body: CommandRequest, request: Request) -> CommandResult:
    return await _orchestrator(request).send_command(window_id, body)


@router.post("/windows/{window_id}/upload", response_model=CommandResult)
async def upload_receipt(window_id: str, file: UploadFile, request: Request) -> CommandResult:
    """Upload a PDF receipt for the given window."""
    # Save to temp file, then send UPLOAD_RECEIPT command with path
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
    ALLOWED_TYPES = {"image/jpeg", "image/png", "application/pdf"}
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail=f"Unsupported type: {file.content_type}")

    suffix = os.path.splitext(file.filename or "receipt.pdf")[1] or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    cmd = CommandRequest(type=CommandType.UPLOAD_RECEIPT, params={"path": tmp_path})
    result = await _orchestrator(request).send_command(window_id, cmd)

    # Cleanup temp file after command (best effort)
    try:
        os.unlink(tmp_path)
    except Exception:
        pass

    return result
