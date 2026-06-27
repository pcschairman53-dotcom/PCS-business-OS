"""
PCS Business OS - User Models and Schemas
File: /app/models/user.py
"""

from datetime import datetime
from typing import Any, List, Optional
from bson import ObjectId
from pydantic import BaseModel, EmailStr, Field, GetCoreSchemaHandler
from pydantic_core import core_schema


class PyObjectId(str):
    """
    Custom type to validate MongoDB ObjectId and serialize it to standard string.
    Fully compatible with Pydantic v2.
    """
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.json_or_python_schema(
            json_schema=core_schema.str_schema(),
            python_schema=core_schema.union_schema([
                core_schema.is_instance_schema(ObjectId),
                core_schema.chain_schema([
                    core_schema.str_schema(),
                    core_schema.no_info_plain_validator_function(lambda v: ObjectId(v)),
                ])
            ]),
            serialization=core_schema.plain_serializer_function_handler(
                lambda x: str(x)
            )
        )


class UserBase(BaseModel):
    """Shared user properties used in creation and updates."""
    email: EmailStr = Field(..., description="User's unique email address")
    full_name: str = Field(..., description="User's full name")
    role: str = Field(default="user", description="Application security role (e.g. user, manager, admin)")
    is_active: bool = Field(default=True, description="Indicates if the user's account is currently active")
    is_superuser: bool = Field(default=False, description="Superuser access flag")


class UserCreate(UserBase):
    """User properties required during registration / creation."""
    password: str = Field(..., min_length=8, description="User password, minimum length of 8 characters")


class UserUpdate(BaseModel):
    """User properties permitted during updates. Fields are optional."""
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=8)


class UserInDB(UserBase):
    """
    Schema representing user as stored inside MongoDB.
    Includes technical metadata and hashed password.
    """
    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
        "json_encoders": {ObjectId: str}
    }


class UserResponse(UserBase):
    """
    Schema for user representation returned to clients.
    Excludes sensitive fields like password.
    """
    id: PyObjectId = Field(..., alias="_id")
    created_at: datetime
    updated_at: datetime

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
        "json_encoders": {ObjectId: str}
    }
