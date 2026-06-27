"""
PCS Business OS - Retrieval-Augmented Generation (RAG) Service
File: /app/services/rag_service.py
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from bson import ObjectId

from app.services.gemini_service import GeminiService
from app.services.embedding_service import EmbeddingService
from app.core.gemini_config import gemini_settings

logger = logging.getLogger("pcs-business-os.rag-service")


class RAGService:
    """
    Coordinates semantic vector search on indexed document chunks
    and grounds Google Gemini text generation inside matching contexts.
    """

    @staticmethod
    async def answer_query(
        db: Any,
        user_query: str,
        uploaded_by: ObjectId,
        conversation_id: Optional[str] = None,
        file_id_filter: Optional[str] = None,
        limit: int = 5
    ) -> Dict[str, Any]:
        """
        Coordinates the execution of a complete RAG operation:
        1. Query Atlas Vector Search to retrieve matching semantic text chunks.
        2. Scaffold the user query with matching context snippets.
        3. Invoke Gemini using focused low-temperature parameters.
        4. Save query/response event history inside MongoDB for audit and rendering.
        """
        try:
            # Parse filters
            file_obj_filter = ObjectId(file_id_filter) if file_id_filter else None
            max_chunks = limit or gemini_settings.MAX_RAG_CONTEXT_CHUNKS

            # 1. Retrieve highly relevant document context chunks
            matching_chunks = await EmbeddingService.vector_search(
                db=db,
                query_text=user_query,
                limit=max_chunks,
                user_id_filter=uploaded_by,
                file_id_filter=file_obj_filter
            )

            # 2. Extract plain text content and construct reference metadata
            context_texts = []
            references = []

            for chunk in matching_chunks:
                context_texts.append(chunk["text"])
                references.append({
                    "file_id": str(chunk["file_id"]),
                    "chunk_index": chunk["chunk_index"],
                    "score": chunk.get("score", 1.0),
                    "snippet": chunk["text"][:150] + "..." if len(chunk["text"]) > 150 else chunk["text"]
                })

            # 3. Request Gemini to answer user query grounded inside matched context
            answer_text = await GeminiService.query_rag(
                user_query=user_query,
                context_chunks=context_texts
            )

            # 4. Record interaction history in the database
            interaction_doc = {
                "user_id": uploaded_by,
                "conversation_id": conversation_id or uuid_placeholder_str(user_query),
                "query": user_query,
                "answer": answer_text,
                "references": references,
                "chunks_used_count": len(matching_chunks),
                "created_at": datetime.utcnow()
            }

            await db.rag_history.insert_one(interaction_doc)

            # Transform MongoDB ObjectId keys for payload response
            interaction_doc["_id"] = str(interaction_doc["_id"])
            interaction_doc["user_id"] = str(interaction_doc["user_id"])

            return {
                "success": True,
                "query": user_query,
                "answer": answer_text,
                "references": references,
                "history_record": interaction_doc
            }

        except Exception as e:
            logger.error(f"Failed to execute RAG query operation: {str(e)}")
            raise ValueError(f"RAG search execution failed: {str(e)}")


def uuid_placeholder_str(seed: str) -> str:
    """Helper to generate a clean, semi-stable placeholder ID if no conversation context is passed."""
    import uuid
    import hashlib
    hash_obj = hashlib.md5(seed.encode("utf-8"))
    return str(uuid.UUID(hash_obj.hexdigest()))
