"""
PCS Business OS - User Management Routes
File: /app/routes/users.py
"""

from datetime import datetime
from typing import List, Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.dependencies import get_current_active_user
from app.core.rbac import HierarchicalRoleChecker, SystemRole, PermissionChecker
from app.models.user import UserResponse, UserUpdate, UserInDB
from app.services.auth_service import AuthService

router = APIRouter()


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile"
)
async def read_user_me(
    current_user: UserResponse = Depends(get_current_active_user)
):
    """
    Retrieves the profile of the currently authenticated user session.
    """
    return current_user


@router.put(
    "/me",
    response_model=UserResponse,
    summary="Update current user profile"
)
async def update_user_me(
    request: Request,
    user_in: UserUpdate,
    current_user: UserResponse = Depends(get_current_active_user)
):
    """
    Allows the authenticated user to update their own profile parameters.
    Saves updates securely back to MongoDB Atlas.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    # Prepare update object
    update_data = user_in.model_dump(exclude_unset=True)
    
    # Restrict security-sensitive parameters for self-service updates
    if "role" in update_data:
        del update_data["role"]
    if "is_active" in update_data:
        del update_data["is_active"]

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid fields provided for profile update."
        )

    # Handle password updates
    if "password" in update_data:
        raw_password = update_data.pop("password")
        update_data["hashed_password"] = AuthService.hash_password(raw_password)

    # Enforce email uniqueness if email is changed
    if "email" in update_data:
        new_email = update_data["email"].lower().strip()
        update_data["email"] = new_email
        if new_email != current_user.email:
            existing = await db.users.find_one({"email": new_email})
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This email address is already in use by another account."
                )

    update_data["updated_at"] = datetime.utcnow()

    # Apply database update
    await db.users.update_one(
        {"_id": ObjectId(current_user.id)},
        {"$set": update_data}
    )

    # Retrieve and return updated user
    updated_user = await db.users.find_one({"_id": ObjectId(current_user.id)})
    return updated_user


@router.get(
    "/",
    response_model=List[UserResponse],
    summary="List all users (Admin/Manager only)",
    dependencies=[Depends(HierarchicalRoleChecker(SystemRole.MANAGER))]
)
async def list_users(
    request: Request,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max number of records to return"),
    role: Optional[str] = Query(None, description="Filter users by system role")
):
    """
    Provides an administrative view of all registered users.
    Supports filtering and offset pagination.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    query = {}
    if role:
        query["role"] = role

    cursor = db.users.find(query).skip(skip).limit(limit).sort("created_at", -1)
    users_list = await cursor.to_list(length=limit)
    return users_list


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get user details by ID (Admin/Manager only)",
    dependencies=[Depends(HierarchicalRoleChecker(SystemRole.MANAGER))]
)
async def read_user_by_id(
    request: Request,
    user_id: str
):
    """
    Retrieves complete details of any registered user using their unique ID.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    try:
        obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided user ID format is invalid."
        )

    user_data = await db.users.find_one({"_id": obj_id})
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested user could not be found."
        )
    return user_data


@router.put(
    "/{user_id}",
    response_model=UserResponse,
    summary="Administrative update of any user (Admin only)",
    dependencies=[Depends(HierarchicalRoleChecker(SystemRole.ADMIN))]
)
async def admin_update_user(
    request: Request,
    user_id: str,
    user_in: UserUpdate
):
    """
    Full administrative access endpoint. Allows updating sensitive fields
    such as roles and active status on any user account.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    try:
        obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided user ID format is invalid."
        )

    # Check existence
    existing_user = await db.users.find_one({"_id": obj_id})
    if not existing_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested user to update does not exist."
        )

    update_data = user_in.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields were provided to update."
        )

    # Hash password if provided
    if "password" in update_data:
        raw_password = update_data.pop("password")
        update_data["hashed_password"] = AuthService.hash_password(raw_password)

    # Handle email uniqueness check
    if "email" in update_data:
        new_email = update_data["email"].lower().strip()
        update_data["email"] = new_email
        if new_email != existing_user["email"]:
            email_conflict = await db.users.find_one({"email": new_email})
            if email_conflict:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This email address is already registered to another user."
                )

    update_data["updated_at"] = datetime.utcnow()

    # Perform update
    await db.users.update_one(
        {"_id": obj_id},
        {"$set": update_data}
    )

    # Return updated user model
    updated_user = await db.users.find_one({"_id": obj_id})
    return updated_user


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a user account (Admin only)",
    dependencies=[Depends(HierarchicalRoleChecker(SystemRole.ADMIN))]
)
async def delete_user(
    request: Request,
    user_id: str,
    current_user: UserResponse = Depends(get_current_active_user)
):
    """
    Deletes a user account from MongoDB Atlas.
    Prevents admins from deleting themselves.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    try:
        obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided user ID format is invalid."
        )

    if str(obj_id) == str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Safety violation: You are not permitted to delete your own administrative account."
        )

    result = await db.users.delete_one({"_id": obj_id})
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested user to delete could not be found."
        )

    return {
        "success": True,
        "message": f"Successfully deleted user account with ID: {user_id}"
    }
