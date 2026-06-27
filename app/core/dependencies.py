"""
PCS Business OS - Security and Authentication Dependencies
File: /app/core/dependencies.py
"""

from typing import List, Optional
from bson import ObjectId
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer

from app.models.user import UserResponse
from app.services.auth_service import AuthService

# Secure token retrieval extraction (both Swagger UI login and manual Header support)
reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False
)

# Optional fallbacks for standard Bearer Token manual extraction
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(reusable_oauth2)
) -> UserResponse:
    """
    Dependency to authenticate and extract the current active user from JWT.
    Supports token retrieval via Authorization Headers.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    # Fallback to check Authorization Header manually if oauth2 scheme missed it
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Decode and validate payload
    payload = AuthService.verify_token(token)
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token: subject missing.",
        )

    try:
        user_object_id = ObjectId(user_id_str)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token: corrupted subject.",
        )

    # Database lookup
    user_data = await db.users.find_one({"_id": user_object_id})
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The user associated with this session no longer exists.",
        )

    # Check active status
    if not user_data.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your user account has been deactivated.",
        )

    # Return validated response model
    return UserResponse(**user_data)


def get_current_active_user(
    current_user: UserResponse = Depends(get_current_user)
) -> UserResponse:
    """
    Guarantees the authenticated user is active.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user session."
        )
    return current_user


class RoleChecker:
    """
    Role-based Access Control (RBAC) dependency factory.
    Enforces required roles for specific route groups.
    """
    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(
        self,
        current_user: UserResponse = Depends(get_current_active_user)
    ) -> UserResponse:
        if current_user.role not in self.allowed_roles and not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation forbidden: requires one of the following roles: {self.allowed_roles}."
            )
        return current_user
