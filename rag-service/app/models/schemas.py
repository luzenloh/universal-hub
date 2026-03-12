import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# --- Document ---

class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., min_length=1)
    source: str | None = None


class DocumentResponse(BaseModel):
    id: uuid.UUID
    title: str
    source: str | None
    chunk_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Query ---

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2048)
    top_k: int = Field(default=5, ge=1, le=20)
    document_id: uuid.UUID | None = None  # optional filter by document


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]  # list of {chunk_id, document_title, content_preview}


# --- Health ---

class HealthResponse(BaseModel):
    status: str
    version: str
