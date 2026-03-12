# All prompts are defined here to keep LLM behavior centralized and auditable.


# RAG answer generation prompt.
# Instructs the model to answer strictly from provided context chunks.
# If the answer is not in context, the model must say so — no hallucinations.
RAG_SYSTEM_PROMPT = """You are a precise question-answering assistant.
You will be given a set of context passages retrieved from a document store.
Answer the user's question using ONLY the provided context.
If the answer cannot be found in the context, respond with:
"I could not find an answer to your question in the provided documents."
Do not speculate or use outside knowledge. Be concise and accurate."""


def build_rag_user_message(query: str, context_chunks: list[str]) -> str:
    """Format retrieved chunks + query into the user message for RAG."""
    formatted_context = "\n\n---\n\n".join(
        f"[Chunk {i + 1}]\n{chunk}" for i, chunk in enumerate(context_chunks)
    )
    return f"Context:\n{formatted_context}\n\nQuestion: {query}"
