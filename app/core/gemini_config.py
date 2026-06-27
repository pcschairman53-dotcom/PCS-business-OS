"""
PCS Business OS - Google Gemini AI Configuration
File: /app/core/gemini_config.py
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GeminiSettings(BaseSettings):
    """
    Configuration settings specifically for Google Gemini models and generation parameters.
    Consistent with the Pydantic Settings implementation used in /app/core/config.py.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore"
    )

    # Core Model Selections (aligned with gemini-api skill instructions)
    DEFAULT_TEXT_MODEL: str = Field(
        default="gemini-3.5-flash",
        description="Default model for basic text, summarization, and standard Q&A tasks"
    )
    COMPLEX_REASONING_MODEL: str = Field(
        default="gemini-3.1-pro-preview",
        description="Model for complex reasoning, advanced coding, or high-accuracy logic"
    )
    EMBEDDING_MODEL: str = Field(
        default="gemini-embedding-2-preview",
        description="Model for generating high-fidelity text vector embeddings"
    )
    IMAGE_GEN_MODEL: str = Field(
        default="gemini-2.5-flash-image",
        description="Default model for image generation tasks"
    )

    # Generation Parameters
    DEFAULT_TEMPERATURE: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Controls randomness of text generation"
    )
    LOW_TEMPERATURE_FOCUSED: float = Field(
        default=0.1,
        description="Focused, low-temperature setting for RAG search or factual Q&A"
    )
    CREATIVE_TEMPERATURE: float = Field(
        default=0.7,
        description="Higher temperature for brainstorming or creative generation"
    )
    
    # Text Chunking Limits (for PDF parsing and Vector search retrieval)
    RAG_CHUNK_SIZE: int = Field(
        default=500,
        description="Number of words per document chunk for indexing vector embeddings"
    )
    RAG_CHUNK_OVERLAP: int = Field(
        default=100,
        description="Word overlap between consecutive chunks to preserve contextual coherence"
    )
    MAX_RAG_CONTEXT_CHUNKS: int = Field(
        default=5,
        description="Maximum number of context chunks retrieved from vector search to present to Gemini"
    )

    # Default System Instructions
    DEFAULT_SYSTEM_INSTRUCTION: str = Field(
        default=(
            "You are PCS Business OS, an expert virtual enterprise assistant. "
            "Always respond in a professional, constructive, and helpful manner."
        )
    )
    RAG_SYSTEM_INSTRUCTION: str = Field(
        default=(
            "You are PCS Business OS, an expert RAG search agent. "
            "Always answer questions truthfully and accurately based strictly on the provided context facts. "
            "If the context does not contain the answer, state that clearly. Do not extrapolate or speculate."
        )
    )


# Singleton configuration instance to be imported across services
gemini_settings = GeminiSettings()
