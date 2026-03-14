import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from bot.services.ws_manager import WebSocketManager

logger = logging.getLogger(__name__)

router = APIRouter()


def make_ws_router(ws_manager: WebSocketManager) -> APIRouter:
    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await ws_manager.connect(websocket)
        try:
            while True:
                # Keep connection alive; client sends pings as plain text
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning("WS error: %s", exc)
        finally:
            ws_manager.disconnect(websocket)

    return router
