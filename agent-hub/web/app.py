from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bot.services.orchestrator import get_orchestrator
from bot.services.ws_manager import WebSocketManager
from web.api import routes, ws
from web.api import agent_routes

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(ws_manager: WebSocketManager, hub_secret: str = "") -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        orchestrator = get_orchestrator()
        app.state.orchestrator = orchestrator
        app.state.hub_secret = hub_secret
        count = await orchestrator.restore_from_cache()
        if count:
            logger.info("Auto-connected %d profiles from cache", count)
        logger.info("FastAPI app ready, orchestrator attached")
        yield
        logger.info("FastAPI shutdown")

    app = FastAPI(title="massmo-controller", lifespan=lifespan)

    app.include_router(routes.router)
    app.include_router(agent_routes.router)

    ws_router = ws.make_ws_router(ws_manager)
    app.include_router(ws_router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/")
        async def index():
            return FileResponse(str(STATIC_DIR / "index.html"))

    return app
