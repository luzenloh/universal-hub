import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bot.db.base import async_session_factory
from bot.services.orchestrator import Orchestrator
from bot.services.ws_manager import WebSocketManager
from web.api import routes, ws

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    ws_manager = WebSocketManager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        orchestrator = Orchestrator(
            session_factory=async_session_factory,
            ws_manager=ws_manager,
        )
        app.state.orchestrator = orchestrator
        app.state.ws_manager = ws_manager
        logger.info("Orchestrator initialized")
        yield
        # Graceful shutdown
        await orchestrator.stop_session()
        logger.info("Orchestrator shutdown complete")

    app = FastAPI(title="massmo-controller", lifespan=lifespan)

    # REST routes
    app.include_router(routes.router)

    # WebSocket route
    ws_router = ws.make_ws_router(ws_manager)
    app.include_router(ws_router)

    # Serve static SPA
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/")
        async def index():
            return FileResponse(str(STATIC_DIR / "index.html"))

    return app
