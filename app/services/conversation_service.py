"""
PCS Business OS - Conversation History and Session Management Service
File: /app/services/conversation_service.py
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from bson import ObjectId

from app.services.gemini_service import GeminiService

logger = logging.getLogger("pcs-business-os.conversation-service")


class ConversationService:
    """
    Coordinating service for managing user chat sessions (conversations), 
    including title generation, retrieval, pinning, and deleting session history.
    """

    @staticmethod
    async def create_conversation(
        db: Any,
        user_id: ObjectId,
        title: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a new conversation session document in the 'conversations' collection.
        """
        try:
            now = datetime.utcnow()
            doc = {
                "user_id": user_id,
                "title": title or "New Conversation",
                "is_pinned": False,
                "created_at": now,
                "updated_at": now
            }
            result = await db.conversations.insert_one(doc)
            doc["_id"] = str(result.inserted_id)
            doc["user_id"] = str(doc["user_id"])
            return doc
        except Exception as e:
            logger.error(f"Failed to create conversation session: {str(e)}")
            raise ValueError(f"Failed to initialize conversation: {str(e)}")

    @staticmethod
    async def get_conversation(
        db: Any,
        conversation_id: str,
        user_id: ObjectId
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single conversation session metadata.
        """
        try:
            query: Dict[str, Any] = {"user_id": user_id}
            
            # Handle stringified ObjectId or standard string/UUID
            try:
                query["_id"] = ObjectId(conversation_id)
            except Exception:
                query["_id"] = conversation_id

            doc = await db.conversations.find_one(query)
            if doc:
                doc["_id"] = str(doc["_id"])
                doc["user_id"] = str(doc["user_id"])
            return doc
        except Exception as e:
            logger.error(f"Error retrieving conversation {conversation_id}: {str(e)}")
            return None

    @staticmethod
    async def list_user_conversations(
        db: Any,
        user_id: ObjectId,
        limit: int = 50,
        skip: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Lists past conversation sessions for a user, ordered by pins and last updated timestamp.
        """
        try:
            query = {"user_id": user_id}
            # Sort first by is_pinned (descending), then updated_at (descending)
            cursor = db.conversations.find(query).sort([
                ("is_pinned", -1),
                ("updated_at", -1)
            ]).skip(skip).limit(limit)

            results = await cursor.to_list(length=limit)
            for doc in results:
                doc["_id"] = str(doc["_id"])
                doc["user_id"] = str(doc["user_id"])
            return results
        except Exception as e:
            logger.error(f"Error listing user conversations: {str(e)}")
            return []

    @staticmethod
    async def update_conversation(
        db: Any,
        conversation_id: str,
        user_id: ObjectId,
        title: Optional[str] = None,
        is_pinned: Optional[bool] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Updates conversation session settings such as title or pinning.
        """
        try:
            query: Dict[str, Any] = {"user_id": user_id}
            try:
                query["_id"] = ObjectId(conversation_id)
            except Exception:
                query["_id"] = conversation_id

            update_fields: Dict[str, Any] = {"updated_at": datetime.utcnow()}
            if title is not None:
                update_fields["title"] = title.strip()
            if is_pinned is not None:
                update_fields["is_pinned"] = is_pinned

            result = await db.conversations.find_one_and_update(
                query,
                {"$set": update_fields},
                return_document=True
            )

            if result:
                result["_id"] = str(result["_id"])
                result["user_id"] = str(result["user_id"])
            return result
        except Exception as e:
            logger.error(f"Failed to update conversation {conversation_id}: {str(e)}")
            raise ValueError(f"Failed to update conversation: {str(e)}")

    @staticmethod
    async def delete_conversation(
        db: Any,
        conversation_id: str,
        user_id: ObjectId
    ) -> bool:
        """
        Permanently deletes a conversation session and cascades deletion 
        to all associated chat message logs inside rag_history.
        """
        try:
            # 1. Delete session metadata
            query: Dict[str, Any] = {"user_id": user_id}
            try:
                query["_id"] = ObjectId(conversation_id)
            except Exception:
                query["_id"] = conversation_id

            session_deleted = await db.conversations.delete_one(query)
            
            # 2. Delete cascaded query message logs inside rag_history
            # Supports conversation_id stored as ObjectId string or regular string
            msg_query = {
                "user_id": user_id,
                "$or": [
                    {"conversation_id": conversation_id},
                    {"conversation_id": str(conversation_id)}
                ]
            }
            messages_deleted = await db.rag_history.delete_many(msg_query)
            
            logger.info(
                f"Permanently cleared conversation {conversation_id}. "
                f"Session cleared: {session_deleted.deleted_count > 0}. "
                f"Cleared {messages_deleted.deleted_count} messages from history."
            )
            return session_deleted.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting conversation {conversation_id}: {str(e)}")
            return False

    @staticmethod
    async def get_conversation_messages(
        db: Any,
        conversation_id: str,
        user_id: ObjectId
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all query-answer logs associated with a conversation thread in chronological order.
        """
        try:
            query = {
                "user_id": user_id,
                "$or": [
                    {"conversation_id": conversation_id},
                    {"conversation_id": str(conversation_id)}
                ]
            }
            cursor = db.rag_history.find(query).sort("created_at", 1)
            results = await cursor.to_list(length=500)
            for doc in results:
                doc["_id"] = str(doc["_id"])
                doc["user_id"] = str(doc["user_id"])
            return results
        except Exception as e:
            logger.error(f"Error fetching conversation messages: {str(e)}")
            return []

    @staticmethod
    async def auto_generate_title(
        db: Any,
        conversation_id: str,
        user_id: ObjectId,
        first_query: str
    ) -> str:
        """
        Uses Gemini to generate a highly professional, 2-to-5 word title 
        summarizing the first query of a conversation thread.
        Updates the conversation metadata in the background.
        """
        try:
            prompt = (
                "Based on this initial user message to a business virtual assistant, "
                "generate a extremely brief, professional chat title. "
                "Strictly return only the title itself, containing 2 to 5 words maximum. "
                "No punctuation, no surrounding quotes.\n\n"
                f"User Message: {first_query}"
            )
            
            # Use gemini-2.5-flash or configured default model
            summary_title = await GeminiService.generate_text(
                prompt=prompt,
                system_instruction="You are a precise business summarizer. Return only 2-5 words.",
                temperature=0.1
            )
            
            # Clean response
            summary_title = summary_title.strip().strip('"').strip("'").strip(".").strip()
            if len(summary_title) > 60:
                summary_title = summary_title[:57] + "..."

            if summary_title:
                await ConversationService.update_conversation(
                    db=db,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    title=summary_title
                )
                return summary_title
            
            return "New Conversation"

        except Exception as e:
            logger.warning(f"Failed to auto-generate conversation title: {str(e)}")
            return "New Conversation"
