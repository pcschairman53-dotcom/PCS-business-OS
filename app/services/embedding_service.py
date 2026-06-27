"""
PCS Business OS - High-Fidelity Vector Embedding and Atlas Vector Search Service
File: /app/services/embedding_service.py
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from bson import ObjectId

from app.services.gemini_service import GeminiService
from app.services.pdf_service import PDFService
from app.core.gemini_config import gemini_settings

logger = logging.getLogger("pcs-business-os.embedding-service")


class EmbeddingService:
    """
    Service coordinating document processing, vector embedding generation via Gemini,
    MongoDB chunk storage, and high-fidelity Atlas Vector Search.
    """

    @staticmethod
    async def embed_text(text: str) -> List[float]:
        """
        Generates a 768-dimension vector embedding for a block of text using Gemini.
        """
        try:
            return await GeminiService.get_embedding(text)
        except Exception as e:
            logger.error(f"Failed to generate vector embedding: {str(e)}")
            raise ValueError(f"Vector embedding generation failed: {str(e)}")

    @staticmethod
    async def process_and_index_file(
        db: Any,
        file_id: ObjectId,
        physical_path: str,
        uploaded_by: ObjectId
    ) -> Dict[str, Any]:
        """
        Extracts, chunks, embeds, and saves document content for RAG search.
        
        1. Extract text from PDF/Document.
        2. Clean and preprocess the text.
        3. Break text down into overlapping semantic chunks.
        4. Generate embeddings for each chunk concurrently.
        5. Insert chunk documents into MongoDB with 'embedding' vector field.
        """
        try:
            # 1. Parse text content from file
            raw_text = await PDFService.extract_text_from_pdf(physical_path)
            if not raw_text or not raw_text.strip():
                logger.warning(f"Extracted document text is empty for file: {file_id}")
                return {"success": False, "chunks_count": 0, "message": "Extracted document text is empty."}

            cleaned_text = PDFService.clean_extracted_text(raw_text)

            # 2. Slice text into overlapping logical chunks
            chunks_data = PDFService.chunk_text(
                cleaned_text,
                chunk_size=gemini_settings.RAG_CHUNK_SIZE,
                chunk_overlap=gemini_settings.RAG_CHUNK_OVERLAP
            )

            if not chunks_data:
                return {"success": False, "chunks_count": 0, "message": "Failed to chunk text contents."}

            # 3. Generate embeddings and construct chunk objects
            chunk_documents = []
            for item in chunks_data:
                chunk_text = item["text"]
                # Generate embedding
                embedding_vector = await EmbeddingService.embed_text(chunk_text)
                
                chunk_documents.append({
                    "file_id": file_id,
                    "chunk_index": item["chunk_index"],
                    "text": chunk_text,
                    "word_count": item["word_count"],
                    "embedding": embedding_vector,
                    "uploaded_by": uploaded_by,
                    "created_at": datetime.utcnow()
                })

            # 4. Save chunks to MongoDB database
            # First, clean existing chunks to prevent duplicate indexes if re-processing
            await db.chunks.delete_many({"file_id": file_id})

            if chunk_documents:
                result = await db.chunks.insert_many(chunk_documents)
                inserted_count = len(result.inserted_ids)
                logger.info(f"Indexed {inserted_count} vector chunks for file ID {file_id}")
                
                # Update files index metadata with processed status
                await db.files.update_one(
                    {"_id": file_id},
                    {"$set": {
                        "is_indexed": True,
                        "indexed_at": datetime.utcnow(),
                        "chunks_count": inserted_count
                    }}
                )
                return {
                    "success": True,
                    "chunks_count": inserted_count,
                    "message": f"Successfully structured and indexed {inserted_count} vector chunks."
                }
            
            return {"success": False, "chunks_count": 0, "message": "No valid chunks structured."}

        except Exception as e:
            logger.error(f"Error encountered during process_and_index_file: {str(e)}")
            # Log the processing failure status on file metadata
            await db.files.update_one(
                {"_id": file_id},
                {"$set": {
                    "is_indexed": False,
                    "indexing_error": str(e)
                }}
            )
            raise ValueError(f"Document embedding process failed: {str(e)}")

    @staticmethod
    async def vector_search(
        db: Any,
        query_text: str,
        limit: int = 5,
        user_id_filter: Optional[ObjectId] = None,
        file_id_filter: Optional[ObjectId] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes MongoDB Atlas Vector Search using standard aggregate pipeline.
        Matches similar chunks to support LLM context augmentation.
        """
        # 1. Generate query embedding vector
        query_vector = await EmbeddingService.embed_text(query_text)
        if not query_vector:
            return []

        # 2. Build MongoDB Atlas vectorSearch stage
        # Supported parameters matched against typical Atlas Vector Search Indexes
        vector_search_stage = {
            "index": "vector_index",      # Standard Atlas vector index configuration name
            "path": "embedding",          # Field inside the chunks documents storing vectors
            "queryVector": query_vector,
            "numCandidates": limit * 10,  # Recommended lookup depth for HNSW algorithm
            "limit": limit
        }

        # Inject filters to safeguard document visibility (tenant/user scoping)
        filter_query: Dict[str, Any] = {}
        if user_id_filter:
            filter_query["uploaded_by"] = user_id_filter
        if file_id_filter:
            filter_query["file_id"] = file_id_filter

        if filter_query:
            vector_search_stage["filter"] = filter_query

        # Construct MongoDB aggregation pipeline
        pipeline = [
            {"$vectorSearch": vector_search_stage},
            # Projection to include score metadata and exclude bulky raw vector
            {
                "$project": {
                    "file_id": 1,
                    "chunk_index": 1,
                    "text": 1,
                    "word_count": 1,
                    "uploaded_by": 1,
                    "created_at": 1,
                    "score": {"$meta": "vectorSearchScore"}
                }
            }
        ]

        try:
            cursor = db.chunks.aggregate(pipeline)
            results = await cursor.to_list(length=limit)
            return results
        except Exception as e:
            # Fallback if Atlas Vector Search is not configured or fails due to sandbox index limitations
            logger.warning(
                f"Atlas Vector Search stage failed: {str(e)}. "
                "Falling back to basic regex text search for system robustness."
            )
            # Safe regex text fallback query
            text_filter: Dict[str, Any] = {"text": {"$regex": query_text, "$options": "i"}}
            if user_id_filter:
                text_filter["uploaded_by"] = user_id_filter
            if file_id_filter:
                text_filter["file_id"] = file_id_filter

            cursor = db.chunks.find(text_filter).limit(limit)
            results = await cursor.to_list(length=limit)
            # Append manual mock score to preserve result shape compatibility
            for res in results:
                res["score"] = 1.0
                if "embedding" in res:
                    del res["embedding"]
            return results

    @staticmethod
    async def delete_document_chunks(db: Any, file_id: ObjectId) -> bool:
        """
        Permanently clears all semantic chunks associated with a deleted document file.
        """
        try:
            result = await db.chunks.delete_many({"file_id": file_id})
            logger.info(f"Cleared {result.deleted_count} chunks associated with file ID: {file_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete document chunks for file ID {file_id}: {str(e)}")
            return False
