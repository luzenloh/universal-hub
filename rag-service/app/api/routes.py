import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.models.schemas import DocumentCreate, DocumentResponse, HealthResponse, QueryRequest, QueryResponse
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


@router.post("/documents", response_model=DocumentResponse, status_code=201, tags=["documents"])
async def ingest_document(
    payload: DocumentCreate,
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    service = RAGService(db)
    try:
        return await service.ingest_document(payload)
    except Exception as e:
        logger.exception("Failed to ingest document")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents", response_model=list[DocumentResponse], tags=["documents"])
async def list_documents(
    db: AsyncSession = Depends(get_db),
) -> list[DocumentResponse]:
    service = RAGService(db)
    return await service.list_documents()


@router.post("/query", response_model=QueryResponse, tags=["query"])
async def query(
    payload: QueryRequest,
    db: AsyncSession = Depends(get_db),
) -> QueryResponse:
    service = RAGService(db)
    try:
        return await service.query(payload)
    except Exception as e:
        logger.exception("Failed to process query")
        raise HTTPException(status_code=500, detail=str(e))
