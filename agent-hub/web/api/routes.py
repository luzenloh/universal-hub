from __future__ import annotations
import logging
import os
import tempfile
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from bot.services.orchestrator import Orchestrator
from web.models.schemas import (
    CommandRequest,
    CommandResult,
    CommandType,
    ConnectRequest,
    InboundState,
    WindowState,
)


class AddProfileRequest(BaseModel):
    label: str


class CreateRequisiteRequest(BaseModel):
    geo: str
    payment_method: str
    bank: str
    requisite: str
    fio: str
    min_limit: float
    max_limit: float
    max_active_orders: int | None = None
    limit: float

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


@router.get("/payfast/orders")
async def get_payfast_orders(request: Request, page: int = 1, limit: int = 20) -> dict:
    """Fetch BT payin orders from PayFast for the dashboard."""
    from bot.services.payfast_client import PayfastClient

    orch = _orchestrator(request)
    secrets = orch._shift_secrets or {}
    pf_secrets = secrets.get("payfast") or {}
    if not pf_secrets.get("email"):
        return {"orders": [], "page": page, "total_pages": 0, "configured": False}

    client = PayfastClient(pf_secrets)
    try:
        data = await client._post(
            "/get_orders_trader",
            {"type": "checks", "page": page, "limit": limit},
        )
        balance_data: dict = {}
        try:
            balance_data = await client.get_balance()
        except Exception:
            pass
        return {
            "orders": data.get("orders") or [],
            "page": page,
            "total_pages": data.get("totalPages", 0),
            "balance": balance_data,
            "configured": True,
        }
    except Exception as exc:
        logger.warning("PayFast orders fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        await client.close()


def _pf_client_from_request(request: Request):
    """Return (PayfastClient, secrets) or raise 503/404."""
    from bot.services.payfast_client import PayfastClient
    orch = _orchestrator(request)
    secrets = orch._shift_secrets or {}
    pf_secrets = secrets.get("payfast") or {}
    if not pf_secrets.get("email"):
        raise HTTPException(status_code=503, detail="PayFast не настроен")
    return PayfastClient(pf_secrets)


@router.get("/payfast/receipt")
async def proxy_payfast_receipt(request: Request, url: str = Query(...)) -> Response:
    """Proxy a PayFast receipt file with Bearer auth so the browser can display it."""
    client = _pf_client_from_request(request)
    try:
        content, content_type = await client.proxy_receipt(unquote(url))
        return Response(content=content, media_type=content_type)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail="Не удалось загрузить чек")
    except Exception as exc:
        logger.warning("PayFast receipt proxy failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        await client.close()


@router.get("/payfast/requisites")
async def get_payfast_requisites(request: Request, status: str = "all") -> dict:
    """List trader requisites from PayFast."""
    client = _pf_client_from_request(request)
    try:
        requisites = await client.get_requisites(status)
        return {"requisites": requisites}
    except Exception as exc:
        logger.warning("PayFast requisites fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        await client.close()


@router.post("/payfast/requisites")
async def create_payfast_requisite(body: CreateRequisiteRequest, request: Request) -> dict:
    """Create a new requisite on PayFast."""
    client = _pf_client_from_request(request)
    try:
        params = body.model_dump(exclude_none=True)
        result = await client.create_requisite(params)
        return result
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except Exception as exc:
        logger.warning("PayFast create_requisite failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        await client.close()


@router.post("/payfast/requisites/{req_id}/archive")
async def archive_payfast_requisite(req_id: str, request: Request) -> dict:
    """Archive a requisite on PayFast."""
    client = _pf_client_from_request(request)
    try:
        await client.archive_requisite(req_id)
        return {"status": "archived"}
    except Exception as exc:
        logger.warning("PayFast archive_requisite failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        await client.close()


@router.post("/payfast/requisites/{req_id}/toggle")
async def toggle_payfast_requisite(req_id: str, request: Request) -> dict:
    """Toggle a requisite active/inactive on PayFast."""
    client = _pf_client_from_request(request)
    try:
        await client.toggle_requisite(req_id)
        return {"status": "toggled"}
    except Exception as exc:
        logger.warning("PayFast toggle_requisite failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        await client.close()


@router.get("/inbound", response_model=list[InboundState])
async def get_inbound(request: Request) -> list[InboundState]:
    """Current state of all InboundControllers (one per active ACTIVE_PAYOUT window)."""
    return _orchestrator(request).get_inbound_states()


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
async def upload_receipt(
    window_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
) -> CommandResult:
    """Upload one or more receipt files for the given window."""
    ALLOWED_TYPES = {"image/jpeg", "image/png", "application/pdf"}
    tmp_paths: list[str] = []
    try:
        for f in files:
            content = await f.read()
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"{f.filename}: file too large (max 10 MB)")
            if f.content_type not in ALLOWED_TYPES:
                raise HTTPException(status_code=415, detail=f"Unsupported type: {f.content_type}")
            suffix = os.path.splitext(f.filename or "receipt.jpg")[1] or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_paths.append(tmp.name)

        cmd = CommandRequest(type=CommandType.UPLOAD_RECEIPT, params={"paths": tmp_paths})
        return await _orchestrator(request).send_command(window_id, cmd)
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except Exception:
                pass


@router.get("/session-info")
async def get_session_info(request: Request) -> dict:
    """Return current session metadata (folder name, etc.)."""
    orch = _orchestrator(request)
    return {"folder_name": orch.get_folder_name()}
