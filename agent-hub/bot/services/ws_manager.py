import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages connected WebSocket clients and broadcasts events."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info("WS client connected (total: %d)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info("WS client disconnected (total: %d)", len(self._clients))

    async def broadcast(self, data: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for client in list(self._clients):
            try:
                await client.send_json(data)
            except Exception:
                dead.append(client)
        for ws in dead:
            self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)
