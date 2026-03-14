import logging
import os
import tempfile

from fastapi import APIRouter, HTTPException, Request, UploadFile

from bot.services.orchestrator import Orchestrator
from web.models.schemas import (
    CommandRequest,
    CommandResult,
    CommandType,
    StartSessionRequest,
    WindowState,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


def _orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/windows", response_model=list[WindowState])
async def get_windows(request: Request) -> list[WindowState]:
    return _orchestrator(request).get_all_states()


@router.post("/session/start", response_model=list[WindowState])
async def start_session(body: StartSessionRequest, request: Request) -> list[WindowState]:
    try:
        return await _orchestrator(request).start_session(body.token_hash, body.profile_count)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("start_session failed")
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
    suffix = os.path.splitext(file.filename or "receipt.pdf")[1] or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
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
