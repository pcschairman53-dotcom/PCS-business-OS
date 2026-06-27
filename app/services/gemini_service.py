"""
PCS Business OS - Google Gemini AI Service
File: /app/services/gemini_service.py
"""

import os
import logging
from typing import List, Optional, Type, Dict, Any
from pydantic import BaseModel

from app.core.gemini_config import gemini_settings

logger = logging.getLogger("pcs-business-os.gemini-service")

# Try to import Google GenAI SDK. If unavailable, we log a warning and fallback.
try:
    from google import genai
    from google.genai import types
    GEMINI_SDK_AVAILABLE = True
except ImportError:
    GEMINI_SDK_AVAILABLE = False
    logger.warning("google-genai Python SDK is not installed. Please run 'pip install google-genai'")


class GeminiService:
    """
    Lazy-initialized service for interfacing with Google Gemini models.
    Supports text generation, structural JSON extraction, and vector embedding generation
    for Retrieval-Augmented Generation (RAG).
    """

    _client: Optional[Any] = None

    @classmethod
    def get_client(cls) -> Any:
        """
        Lazy-initializes and caches the Google GenAI Client.
        Ensures the application does not crash on startup if GEMINI_API_KEY is missing.
        """
        if not GEMINI_SDK_AVAILABLE:
            raise RuntimeError(
                "The Google GenAI SDK is not available. Please install 'google-genai' and verify dependencies."
            )

        if cls._client is None:
            # Look for the runtime-injected key or standard environment variable
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                logger.error("GEMINI_API_KEY environment variable is not set. Unable to initialize client.")
                raise ValueError("GEMINI_API_KEY is missing. Configure it in your environment or secrets.")

            try:
                logger.info("Initializing Google GenAI Client...")
                # The modern client initialized using standard google-genai package syntax
                cls._client = genai.Client(api_key=api_key)
                logger.info("Google GenAI Client successfully configured.")
            except Exception as e:
                logger.error(f"Failed to initialize Google GenAI Client: {str(e)}")
                raise e

        return cls._client

    @classmethod
    async def get_embedding(cls, text: str, model: Optional[str] = None) -> List[float]:
        """
        Generates a vector embedding for a piece of text to be indexed in MongoDB Atlas Vector Search.
        """
        if not text or not text.strip():
            return []

        embedding_model = model or gemini_settings.EMBEDDING_MODEL

        try:
            client = cls.get_client()
            
            # Using client.models.embed_content according to standard google-genai Python SDK guidelines
            response = client.models.embed_content(
                model=embedding_model,
                contents=text
            )

            if response and response.embeddings:
                # Retrieve the values list from the first generated embedding response
                return response.embeddings[0].values
            
            raise ValueError("No embedding vector returned from Gemini API.")

        except Exception as e:
            logger.error(f"Error generating text embedding using model {embedding_model}: {str(e)}")
            raise ValueError(f"Embedding generation failed: {str(e)}")

    @classmethod
    async def generate_text(
        cls, 
        prompt: str, 
        system_instruction: Optional[str] = None, 
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> str:
        """
        Sends a standard context prompt to Gemini and retrieves the generated text.
        """
        target_model = model or gemini_settings.DEFAULT_TEXT_MODEL
        target_temp = temperature if temperature is not None else gemini_settings.DEFAULT_TEMPERATURE
        system_inst = system_instruction or gemini_settings.DEFAULT_SYSTEM_INSTRUCTION

        try:
            client = cls.get_client()

            # Prepare standard configuration options
            config_args: Dict[str, Any] = {
                "temperature": target_temp,
                "system_instruction": system_inst,
            }

            config = types.GenerateContentConfig(**config_args)

            response = client.models.generate_content(
                model=target_model,
                contents=prompt,
                config=config
            )

            if response and response.text:
                return response.text.strip()

            raise ValueError("Gemini returned an empty text response.")

        except Exception as e:
            logger.error(f"Error executing generate_text: {str(e)}")
            raise ValueError(f"AI content generation failed: {str(e)}")

    @classmethod
    async def generate_structured_output(
        cls,
        prompt: str,
        response_schema: Type[BaseModel],
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> Any:
        """
        Enforces a structured JSON output matching a specific Pydantic response schema.
        Perfect for extracting clean business objects, invoice details, or resumes.
        """
        target_model = model or gemini_settings.DEFAULT_TEXT_MODEL
        target_temp = temperature if temperature is not None else gemini_settings.LOW_TEMPERATURE_FOCUSED
        system_inst = system_instruction or gemini_settings.DEFAULT_SYSTEM_INSTRUCTION

        try:
            client = cls.get_client()

            config = types.GenerateContentConfig(
                temperature=target_temp,
                system_instruction=system_inst,
                response_mime_type="application/json",
                response_schema=response_schema
            )

            response = client.models.generate_content(
                model=target_model,
                contents=prompt,
                config=config
            )

            if response and response.text:
                # The SDK automatically handles structure mapping, but we parse it or let the client parse
                # Let's return the parsed Pydantic object
                return response_schema.model_validate_json(response.text)

            raise ValueError("Empty response returned during structured generation.")

        except Exception as e:
            logger.error(f"Error in generate_structured_output: {str(e)}")
            raise ValueError(f"Structured extraction failed: {str(e)}")

    @classmethod
    async def query_rag(
        cls,
        user_query: str,
        context_chunks: List[str],
        system_instruction: Optional[str] = None,
        model: Optional[str] = None
    ) -> str:
        """
        Executes a context-constrained RAG prompt using matching document chunks from Vector Search.
        Prevents hallucination by keeping Gemini focused strictly on the supplied context window.
        """
        target_model = model or gemini_settings.DEFAULT_TEXT_MODEL
        system_inst = system_instruction or gemini_settings.RAG_SYSTEM_INSTRUCTION

        if not context_chunks:
            # Fallback to standard model response but alert user context is missing
            prompt = (
                f"User Question: {user_query}\n\n"
                "[Warning: No matching local context document chunks were found in the Database.]"
            )
        else:
            formatted_context = "\n\n".join(
                f"[Document Chunk {idx + 1}]:\n{chunk}" 
                for idx, chunk in enumerate(context_chunks)
            )
            prompt = (
                "You are an intelligent business virtual assistant. Answer the user question strictly using the document context provided below.\n"
                "If the context does not contain the answer, politely state that the answer is not present in the uploaded reference files.\n\n"
                f"--- CONTEXT REFERENCE DOCUMENTS ---\n{formatted_context}\n\n"
                f"User Question: {user_query}\n"
                "Helpful, grounded response:"
            )

        return await cls.generate_text(
            prompt=prompt,
            system_instruction=system_inst,
            model=target_model,
            temperature=gemini_settings.LOW_TEMPERATURE_FOCUSED
        )

