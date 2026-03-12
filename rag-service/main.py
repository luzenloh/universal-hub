import logging

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import settings
from app.core.database import create_tables

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="RAG Service",
    description="Production RAG backend — document ingestion, vector search, LLM-powered Q&A",
    version="0.1.0",
)

app.include_router(router, prefix="/api/v1")


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Starting up RAG Service...")
    await create_tables()
    logger.info("Database tables ready.")
