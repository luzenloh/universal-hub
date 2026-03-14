import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bot.services.orchestrator import get_orchestrator
from bot.services.ws_manager import WebSocketManager
from web.api import routes, ws

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(ws_manager: WebSocketManager) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        orchestrator = get_orchestrator()
        app.state.orchestrator = orchestrator
        logger.info("FastAPI app ready, orchestrator attached")
        yield
        logger.info("FastAPI shutdown")

    app = FastAPI(title="massmo-controller", lifespan=lifespan)

    app.include_router(routes.router)

    ws_router = ws.make_ws_router(ws_manager)
    app.include_router(ws_router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/")
        async def index():
            return FileResponse(str(STATIC_DIR / "index.html"))

    return app
