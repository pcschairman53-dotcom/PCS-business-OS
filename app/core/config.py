"""
PCS Business OS - Configuration Settings Management
File: /app/core/config.py
"""

from typing import Any, Dict, List, Optional, Union
from pydantic import AnyHttpUrl, BeforeValidator, Field, MongoDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Annotated


def parse_cors_origins(v: Any) -> List[str]:
    """
    Parses CORS origins from a string or list.
    Allows comma-separated strings to be split into list elements.
    """
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, (list, str)):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    """
    Application settings class using Pydantic Settings (Pydantic v2).
    Loads variables from Environment or .env files with validation.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore"
    )

    # FastAPI settings
    PROJECT_NAME: str = Field(default="PCS Business OS")
    ENV: str = Field(default="development")  # development, production, testing
    PORT: int = Field(default=3000)
    API_V1_STR: str = Field(default="/api/v1")

    # CORS Settings
    BACKEND_CORS_ORIGINS: Annotated[
        List[str], BeforeValidator(parse_cors_origins)
    ] = Field(default=["http://localhost:3000", "http://localhost:5173"])

    # Security & Authentication
    JWT_SECRET_KEY: str = Field(
        default="your-super-secret-jwt-key-change-this-in-production"
    )
    JWT_ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60)

    # MongoDB Database Settings
    MONGO_URI: Optional[str] = Field(default=None)
    MONGO_DB_NAME: str = Field(default="pcs_business_os")

    # Google Gemini AI API Settings
    GEMINI_API_KEY: Optional[str] = Field(default=None)

    # App Deployment URL
    APP_URL: str = Field(default="http://localhost:3000")

    @property
    def is_production(self) -> bool:
        """Helper property to check if current environment is production."""
        return self.ENV.lower() == "production"


# Singleton instance to be imported across the application
settings = Settings()
