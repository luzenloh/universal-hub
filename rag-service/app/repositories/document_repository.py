import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import Chunk, Document

logger = logging.getLogger(__name__)


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_document(
        self, title: str, source: str | None, chunks_data: list[dict]
    ) -> Document:
        document = Document(title=title, source=source)
        self.session.add(document)
        await self.session.flush()

        for i, chunk_data in enumerate(chunks_data):
            chunk = Chunk(
                document_id=document.id,
                content=chunk_data["content"],
                chunk_index=i,
                embedding=chunk_data["embedding"],
            )
            self.session.add(chunk)

        await self.session.commit()
        await self.session.refresh(document)
        return document

    async def list_documents(self) -> list[tuple[Document, int]]:
        stmt = (
            select(Document, func.count(Chunk.id).label("chunk_count"))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .group_by(Document.id)
            .order_by(Document.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.all())

    async def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int,
        document_id: uuid.UUID | None = None,
    ) -> list[tuple[Chunk, str]]:
        stmt = (
            select(Chunk, Document.title)
            .join(Document, Document.id == Chunk.document_id)
            .order_by(Chunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        )
        if document_id is not None:
            stmt = stmt.where(Chunk.document_id == document_id)

        result = await self.session.execute(stmt)
        return list(result.all())
