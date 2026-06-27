"""
PCS Business OS - Authentication Routes
File: /app/routes/auth.py
"""

from datetime import datetime
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr, Field

from app.models.user import UserCreate, UserInDB, UserResponse
from app.services.auth_service import AuthService

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login-form", auto_error=False)


class LoginPayload(BaseModel):
    """Payload schema for JSON-based login requests."""
    email: EmailStr = Field(..., description="User's email address")
    password: str = Field(..., description="User's clear password")


class TokenResponse(BaseModel):
    """Schema representing the standard OAuth2 token response."""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account"
)
async def register(request: Request, user_in: UserCreate):
    """
    Asynchronously creates a new user account.
    Checks for email conflicts, hashes the password, and stores user in MongoDB Atlas.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently unavailable."
        )

    # Normalize email to prevent duplication based on casing
    normalized_email = user_in.email.lower().strip()

    # Query for existing user
    existing_user = await db.users.find_one({"email": normalized_email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email address is already registered."
        )

    # Hash the plain text password
    hashed_password = AuthService.hash_password(user_in.password)

    # Create UserInDB schema
    user_db_dict = user_in.model_dump(exclude={"password"})
    user_db_dict["email"] = normalized_email
    user_db_dict["hashed_password"] = hashed_password
    user_db_dict["created_at"] = datetime.utcnow()
    user_db_dict["updated_at"] = datetime.utcnow()

    # Insert into database
    result = await db.users.insert_one(user_db_dict)
    
    # Retrieve the newly inserted user
    new_user = await db.users.find_one({"_id": result.inserted_id})
    if not new_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user after registration."
        )

    return new_user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="User login (JSON-based)"
)
async def login(request: Request, payload: LoginPayload):
    """
    Authenticates a user via email and password.
    Returns a secure JWT access token on success.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently unavailable."
        )

    normalized_email = payload.email.lower().strip()
    user_data = await db.users.find_one({"email": normalized_email})

    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password."
        )

    # Verify password
    if not AuthService.verify_password(payload.password, user_data["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password."
        )

    # Check if active
    if not user_data.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This user account has been deactivated."
        )

    # Generate JWT
    access_token = AuthService.create_access_token(
        subject=str(user_data["_id"]),
        additional_claims={"role": user_data.get("role", "user")}
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user_data
    }
