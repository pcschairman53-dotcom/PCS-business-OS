"""
PCS Business OS - Extended User Profile & Preferences Models
File: /app/models/profile.py
"""

from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from app.models.user import PyObjectId


class NotificationPreferences(BaseModel):
    """Notification settings for user accounts."""
    email_alerts: bool = Field(default=True, description="Receive automated email reports and updates")
    push_notifications: bool = Field(default=False, description="Enable micro-notifications in-app")
    weekly_digest: bool = Field(default=True, description="Receive weekly performance reports")


class WorkspaceSettings(BaseModel):
    """Personalized workspace layout and visualization preferences."""
    theme: str = Field(default="light", description="Visual theme preference (light, dark, system)")
    dashboard_layout: str = Field(default="default", description="Custom dashboard structural layout preset")
    default_language: str = Field(default="en", description="UI localized language selection")


class ProfileBase(BaseModel):
    """Shared properties for detailed business profiles."""
    phone_number: Optional[str] = Field(default=None, description="Contact phone number")
    company_name: Optional[str] = Field(default=None, description="Associated company name")
    job_title: Optional[str] = Field(default=None, description="User's professional title/role inside company")
    department: Optional[str] = Field(default=None, description="Corporate department name")
    bio: Optional[str] = Field(default=None, max_length=500, description="Short user bio")
    notifications: NotificationPreferences = Field(default_factory=NotificationPreferences)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)


class ProfileUpdate(BaseModel):
    """Permitted properties during profile updates. All fields optional."""
    phone_number: Optional[str] = None
    company_name: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    bio: Optional[str] = Field(None, max_length=500)
    notifications: Optional[NotificationPreferences] = None
    workspace: Optional[WorkspaceSettings] = None


class ProfileInDB(ProfileBase):
    """
    Detailed profile document structure inside MongoDB.
    Linked to a user document via user_id.
    """
    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    user_id: PyObjectId = Field(..., description="Foreign key reference linking to users collection")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
    }


class ProfileResponse(ProfileBase):
    """Response payload containing detailed profile schema."""
    id: PyObjectId = Field(..., alias="_id")
    user_id: PyObjectId
    created_at: datetime
    updated_at: datetime

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
    }
