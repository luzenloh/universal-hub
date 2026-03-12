import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.llm.client import complete
from app.llm.prompts import RAG_SYSTEM_PROMPT, build_rag_user_message
from app.models.schemas import DocumentCreate, DocumentResponse, QueryRequest, QueryResponse
from app.repositories.document_repository import DocumentRepository
from app.services.embedding_service import chunk_text, embed_texts

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = DocumentRepository(session)

    async def ingest_document(self, payload: DocumentCreate) -> DocumentResponse:
        logger.info("Ingesting document: %s", payload.title)

        chunks = chunk_text(payload.content, settings.chunk_size, settings.chunk_overlap)
        logger.info("Split into %d chunks", len(chunks))

        embeddings = embed_texts(chunks)

        chunks_data = [
            {"content": chunk, "embedding": embedding}
            for chunk, embedding in zip(chunks, embeddings)
        ]

        document = await self.repo.create_document(
            title=payload.title,
            source=payload.source,
            chunks_data=chunks_data,
        )

        return DocumentResponse(
            id=document.id,
            title=document.title,
            source=document.source,
            chunk_count=len(chunks),
            created_at=document.created_at,
        )

    async def list_documents(self) -> list[DocumentResponse]:
        rows = await self.repo.list_documents()
        return [
            DocumentResponse(
                id=doc.id,
                title=doc.title,
                source=doc.source,
                chunk_count=count,
                created_at=doc.created_at,
            )
            for doc, count in rows
        ]

    async def query(self, payload: QueryRequest) -> QueryResponse:
        logger.info("Processing RAG query: %s", payload.query)

        query_embedding = embed_texts([payload.query])[0]

        results = await self.repo.similarity_search(
            query_embedding=query_embedding,
            top_k=payload.top_k,
            document_id=payload.document_id,
        )

        if not results:
            return QueryResponse(answer="No relevant documents found.", sources=[])

        context_texts = [chunk.content for chunk, _ in results]
        user_message = build_rag_user_message(payload.query, context_texts)
        answer = await complete(RAG_SYSTEM_PROMPT, user_message)

        sources = [
            {
                "chunk_id": str(chunk.id),
                "document_title": title,
                "content_preview": chunk.content[:200],
            }
            for chunk, title in results
        ]

        return QueryResponse(answer=answer, sources=sources)
