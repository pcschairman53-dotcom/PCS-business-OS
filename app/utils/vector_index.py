"""
PCS Business OS - MongoDB Atlas Vector Search Programmatic Index Setup
File: /app/utils/vector_index.py
"""

import logging
from typing import Any, Dict, List, Optional
from pymongo.errors import OperationFailure

logger = logging.getLogger("pcs-business-os.vector-index")

# Default configuration parameters matching Gemini Embedding dimensionalities
DEFAULT_DIMENSION = 768  # text-embedding-004 output dimension
INDEX_NAME = "vector_index"


class AtlasVectorIndexManager:
    """
    Handles the programmatic creation, inspection, and verification of 
    MongoDB Atlas Vector Search indexes on the file chunk collections.
    """

    @staticmethod
    def get_vector_index_definition(dimensions: int = DEFAULT_DIMENSION) -> Dict[str, Any]:
        """
        Generates the standard search index structure for MongoDB Atlas Vector Search.
        Includes fields for exact filtering on owner (uploaded_by) and container document (file_id)
        to enforce tenant isolation and fast contextual scoping during queries.
        """
        return {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": dimensions,
                    "similarity": "cosine"
                },
                {
                    "type": "filter",
                    "path": "uploaded_by"
                },
                {
                    "type": "filter",
                    "path": "file_id"
                }
            ]
        }

    @staticmethod
    async def create_vector_search_index(
        db: Any, 
        collection_name: str = "chunks", 
        dimensions: int = DEFAULT_DIMENSION
    ) -> Dict[str, Any]:
        """
        Programmatically issues the creation command for the Atlas Vector Search Index.
        Operates asynchronously with Motor, executing defensively against non-Atlas environments
        or restricted service accounts.
        """
        if db is None:
            logger.error("Database connection is offline. Cannot initialize search indexes.")
            return {"success": False, "error": "Database is offline"}

        collection = db[collection_name]
        index_definition = AtlasVectorIndexManager.get_vector_index_definition(dimensions)

        logger.info(f"Attempting programmatic creation of Atlas Search Index '{INDEX_NAME}' on '{collection_name}'...")

        try:
            # 1. Attempt using PyMongo's modern search index helper (supported in Motor 3.3.0+ / PyMongo 4.5.0+)
            if hasattr(collection, "create_search_index"):
                # Define search index model
                result = await collection.create_search_index(
                    model={
                        "name": INDEX_NAME,
                        "definition": index_definition
                    }
                )
                logger.info(f"Successfully initiated creation of Atlas search index. Response: {result}")
                return {
                    "success": True,
                    "method": "helper_function",
                    "index_name": INDEX_NAME,
                    "message": "Atlas Search Index creation requested successfully."
                }
            
            # 2. Fallback to issuing raw database administration command if the helper is not exposed
            command = {
                "createSearchIndexes": collection_name,
                "indexes": [
                    {
                        "name": INDEX_NAME,
                        "definition": index_definition
                    }
                ]
            }
            result = await db.command(command)
            logger.info(f"Successfully initiated creation via raw db command. Response: {result}")
            return {
                "success": True,
                "method": "raw_command",
                "index_name": INDEX_NAME,
                "message": "Atlas Search Index creation requested successfully."
            }

        except OperationFailure as e:
            # Handle standard permission errors or community-edition server limits
            logger.warning(
                f"OperationFailure while creating Atlas search index: {str(e)}. "
                "This usually indicates the MongoDB instance is a local community edition or credentials lack Index Admin privileges."
            )
            return {
                "success": False,
                "error": "AtlasSearchUnsupported",
                "details": str(e),
                "message": (
                    "Programmatic Atlas Search Index creation is not supported on this instance. "
                    "If you are using MongoDB Atlas, configure the Search Index manually using the Atlas UI or CLI."
                )
            }
        except Exception as e:
            logger.error(f"Unexpected error when creating Atlas Search Index: {str(e)}")
            return {
                "success": False,
                "error": type(e).__name__,
                "details": str(e),
                "message": "An unexpected failure occurred while preparing vector index."
            }

    @staticmethod
    async def list_active_search_indexes(db: Any, collection_name: str = "chunks") -> List[Dict[str, Any]]:
        """
        Retrieves a list of search indexes currently defined or building on the collection.
        Useful for auditing vector search status dynamically.
        """
        if db is None:
            return []

        collection = db[collection_name]
        try:
            if hasattr(collection, "list_search_indexes"):
                cursor = await collection.list_search_indexes()
                return await cursor.to_list(length=10)
            
            # Raw command fallback
            command = {"listSearchIndexes": collection_name}
            result = await db.command(command)
            return result.get("cursor", {}).get("firstBatch", [])
        except Exception as e:
            logger.warning(f"Could not retrieve active search indexes: {str(e)}")
            return []

    @staticmethod
    async def verify_index_ready(db: Any, collection_name: str = "chunks") -> bool:
        """
        Audits search indexes and returns True if 'vector_index' is completely built
        and ready to accept query requests.
        """
        indexes = await AtlasVectorIndexManager.list_active_search_indexes(db, collection_name)
        for idx in indexes:
            if idx.get("name") == INDEX_NAME:
                # 'queryable' status in MongoDB Atlas denotes the index is fully built and active
                return idx.get("status") == "READY" or idx.get("queryable", False)
        return False
