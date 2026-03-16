from __future__ import annotations
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from bot.services.orchestrator import get_orchestrator
from bot.services.ws_manager import WebSocketManager

logger = logging.getLogger(__name__)

router = APIRouter()


def make_ws_router(ws_manager: WebSocketManager) -> APIRouter:
    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await ws_manager.connect(websocket)

        # Send current state immediately so the page doesn't wait for next poll
        try:
            states = get_orchestrator().get_all_states()
            if states:
                await websocket.send_json({
                    "event": "state_snapshot",
                    "windows": [s.model_dump() for s in states],
                })
        except Exception as exc:
            logger.warning("Failed to send initial snapshot: %s", exc)

        try:
            while True:
                await asyncio.sleep(25)
                await websocket.send_json({"event": "ping"})
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning("WS connection lost: %s", exc)
        finally:
            ws_manager.disconnect(websocket)

    return router
