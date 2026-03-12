import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.models.schemas import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


# Placeholders — будут реализованы в следующих подзадачах
@router.post("/documents", tags=["documents"])
async def ingest_document() -> JSONResponse:
    return JSONResponse({"detail": "not implemented yet"}, status_code=501)


@router.get("/documents", tags=["documents"])
async def list_documents() -> JSONResponse:
    return JSONResponse({"detail": "not implemented yet"}, status_code=501)


@router.post("/query", tags=["query"])
async def query() -> JSONResponse:
    return JSONResponse({"detail": "not implemented yet"}, status_code=501)
