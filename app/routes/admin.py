"""
PCS Business OS - Administrative Controls and System Metrics Routes
File: /app/routes/admin.py
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.core.dependencies import RoleChecker
from app.models.user import UserResponse, PyObjectId
from app.utils.vector_index import AtlasVectorIndexManager

logger = logging.getLogger("pcs-business-os.routes.admin")

router = APIRouter()


class AdminUserUpdatePayload(BaseModel):
    """Payload to modify any user properties, restricted to system administrators."""
    email: Optional[EmailStr] = Field(default=None, description="New email address")
    full_name: Optional[str] = Field(default=None, description="New full name")
    role: Optional[str] = Field(default=None, description="New security role (user, manager, admin)")
    is_active: Optional[bool] = Field(default=None, description="Set account active or deactivated")
    is_superuser: Optional[bool] = Field(default=None, description="Promote or demote superuser status")


class SystemMetricsResponse(BaseModel):
    """Metrics reporting database collections usage and status."""
    success: bool = True
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    users_count: int
    conversations_count: int
    files_count: int
    chunks_count: int
    rag_history_count: int
    environment: str
    database_online: bool


class VectorSearchStatusResponse(BaseModel):
    """Search index readiness and configurations verification response."""
    success: bool
    index_name: str
    collection: str
    is_ready: bool
    indexes_active: List[Dict[str, Any]]


@router.get(
    "/users",
    response_model=List[UserResponse],
    summary="List all registered system users (Admin only)"
)
async def admin_list_users(
    request: Request,
    role: Optional[str] = Query(None, description="Filter list by security role"),
    is_active: Optional[bool] = Query(None, description="Filter list by active status"),
    search: Optional[str] = Query(None, description="Regex match on email or full_name"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=250),
    current_admin: UserResponse = Depends(RoleChecker(["admin"]))
):
    """
    Returns a comprehensive list of all users registered inside the database.
    Supports filtering by role, status, text search matching, and standard pagination.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    # Compile query document based on filters
    query: Dict[str, Any] = {}
    if role:
        query["role"] = role
    if is_active is not None:
        query["is_active"] = is_active
    if search:
        query["$or"] = [
            {"email": {"$regex": search, "$options": "i"}},
            {"full_name": {"$regex": search, "$options": "i"}}
        ]

    try:
        cursor = db.users.find(query).sort("created_at", -1).skip(skip).limit(limit)
        results = await cursor.to_list(length=limit)
        return results
    except Exception as e:
        logger.error(f"Admin failed to fetch users list: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve registered users."
        )


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Modify any user properties (Admin only)"
)
async def admin_update_user(
    request: Request,
    user_id: str,
    payload: AdminUserUpdatePayload,
    current_admin: UserResponse = Depends(RoleChecker(["admin"]))
):
    """
    Allows system administrators to directly update any fields of a user document,
    including role promotion/demotion, superuser flags, or account deactivation.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    try:
        target_obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid target user ID format."
        )

    # 1. Fetch current target user state
    target_user = await db.users.find_one({"_id": target_obj_id})
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The target user account could not be found."
        )

    # 2. Prevent self-demotion or self-deactivation to avoid system lockout scenarios
    if str(target_obj_id) == str(current_admin.id):
        if payload.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Administrative safety policy: You cannot deactivate your own administrative account."
            )
        if payload.role is not None and payload.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Administrative safety policy: You cannot demote your own security role."
            )

    # 3. Construct update body
    update_data: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    
    if payload.email is not None:
        # Check if email is already taken by another user
        existing_email_owner = await db.users.find_one({
            "email": payload.email,
            "_id": {"$ne": target_obj_id}
        })
        if existing_email_owner:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Email address '{payload.email}' is already registered to another user."
            )
        update_data["email"] = payload.email

    if payload.full_name is not None:
        update_data["full_name"] = payload.full_name.strip()

    if payload.role is not None:
        allowed_roles = {"user", "manager", "admin"}
        if payload.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid security role. Allowed values: {allowed_roles}"
            )
        update_data["role"] = payload.role

    if payload.is_active is not None:
        update_data["is_active"] = payload.is_active

    if payload.is_superuser is not None:
        update_data["is_superuser"] = payload.is_superuser

    try:
        updated_user = await db.users.find_one_and_update(
            {"_id": target_obj_id},
            {"$set": update_data},
            return_document=True
        )
        logger.info(f"Admin updated user account {user_id}: {update_data}")
        return updated_user
    except Exception as e:
        logger.error(f"Failed to update user account {user_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while committing user modifications."
        )


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Permanently delete user and cascade all associated data (Admin only)"
)
async def admin_delete_user(
    request: Request,
    user_id: str,
    current_admin: UserResponse = Depends(RoleChecker(["admin"]))
):
    """
    Clears out a user entirely from the system.
    This operation executes a cascading deletion of:
    1. The main user document.
    2. All uploaded files and their corresponding physical disk files.
    3. Indexed vector embedding chunks associated with those files.
    4. Conversation history sessions and message logs in the RAG logs.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    try:
        target_obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid target user ID format."
        )

    # 1. Fetch user to verify existence and protect superusers / admins from accidental erasure
    target_user = await db.users.find_one({"_id": target_obj_id})
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The target user account was not found."
        )

    if str(target_obj_id) == str(current_admin.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrative safety policy: You cannot delete your own active account."
        )

    if target_user.get("is_superuser", False) and not current_admin.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only a system superuser can delete another superuser account."
        )

    try:
        # Cascade Step 2: Retrieve and delete physical uploaded documents
        cursor = db.files.find({"uploaded_by": target_obj_id})
        user_files = await cursor.to_list(length=1000)
        
        deleted_physical_files = 0
        for file_doc in user_files:
            path = file_doc.get("storage_path")
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    deleted_physical_files += 1
                except Exception as file_err:
                    logger.warning(f"Failed to remove physical file '{path}' during cascade deletion: {str(file_err)}")

        # Cascade Step 3: Delete indexed metadata records for files and document chunks
        files_del_result = await db.files.delete_many({"uploaded_by": target_obj_id})
        chunks_del_result = await db.chunks.delete_many({"uploaded_by": target_obj_id})

        # Cascade Step 4: Delete conversation metadata and logs
        conversations_del_result = await db.conversations.delete_many({"user_id": target_obj_id})
        rag_del_result = await db.rag_history.delete_many({"user_id": target_obj_id})

        # Cascade Step 5: Delete primary user account document
        user_del_result = await db.users.delete_one({"_id": target_obj_id})

        logger.critical(
            f"ADMIN CASCADE DELETED user ID '{user_id}' email '{target_user.get('email')}'. "
            f"Physical files: {deleted_physical_files}, Metadata entries: {files_del_result.deleted_count}, "
            f"Vector chunks: {chunks_del_result.deleted_count}, Chat sessions: {conversations_del_result.deleted_count}, "
            f"RAG messages: {rag_del_result.deleted_count}."
        )

        return {
            "success": True,
            "message": f"Successfully cascade deleted user account {user_id} and purged all associated system data.",
            "metrics": {
                "user_account_cleared": user_del_result.deleted_count > 0,
                "physical_files_removed": deleted_physical_files,
                "metadata_files_deleted": files_del_result.deleted_count,
                "vector_chunks_deleted": chunks_del_result.deleted_count,
                "conversations_deleted": conversations_del_result.deleted_count,
                "rag_logs_deleted": rag_del_result.deleted_count
            }
        }

    except Exception as e:
        logger.critical(f"Cascade deletion failure for user {user_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred during cascade data purging: {str(e)}"
        )


@router.get(
    "/metrics",
    response_model=SystemMetricsResponse,
    summary="Gather database usage metrics and status (Admin only)"
)
async def admin_get_system_metrics(
    request: Request,
    current_admin: UserResponse = Depends(RoleChecker(["admin"]))
):
    """
    Analyzes MongoDB collections to calculate statistics and telemetry data,
    including totals of users, active chats, uploaded files, and vector chunks.
    """
    db = request.app.state.db
    if db is None:
        return SystemMetricsResponse(
            users_count=0,
            conversations_count=0,
            files_count=0,
            chunks_count=0,
            rag_history_count=0,
            environment=os.getenv("ENV", "production"),
            database_online=False
        )

    try:
        users_count = await db.users.count_documents({})
        conversations_count = await db.conversations.count_documents({})
        files_count = await db.files.count_documents({})
        chunks_count = await db.chunks.count_documents({})
        rag_history_count = await db.rag_history.count_documents({})

        return SystemMetricsResponse(
            users_count=users_count,
            conversations_count=conversations_count,
            files_count=files_count,
            chunks_count=chunks_count,
            rag_history_count=rag_history_count,
            environment=os.getenv("ENV", "production"),
            database_online=True
        )
    except Exception as e:
        logger.error(f"Failed to compile administrator stats metrics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to query database metric status."
        )


@router.post(
    "/vector-search/reindex",
    summary="Trigger programmatic Atlas Search vector index creation (Admin only)"
)
async def admin_create_vector_index(
    request: Request,
    current_admin: UserResponse = Depends(RoleChecker(["admin"]))
):
    """
    Command to trigger the creation of MongoDB Atlas Search indexes.
    Executes administrative creation commands for standard and vector similarity search layouts.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is offline."
        )

    result = await AtlasVectorIndexManager.create_vector_search_index(db)
    if not result.get("success"):
        # If the programmatic command is unsupported due to local instance or insufficient privileges
        if result.get("error") == "AtlasSearchUnsupported":
            return {
                "success": False,
                "message": result.get("message"),
                "details": result.get("details")
            }
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message")
        )

    return result


@router.get(
    "/vector-search/status",
    response_model=VectorSearchStatusResponse,
    summary="Inspect index definition build readiness (Admin only)"
)
async def admin_get_index_status(
    request: Request,
    current_admin: UserResponse = Depends(RoleChecker(["admin"]))
):
    """
    Inspects search index status on the chunks collection. Reports the active search indexes
    and whether the vector index is queryable and ready to resolve embeddings.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is offline."
        )

    active_indexes = await AtlasVectorIndexManager.list_active_search_indexes(db)
    is_ready = await AtlasVectorIndexManager.verify_index_ready(db)

    return VectorSearchStatusResponse(
        success=True,
        index_name="vector_index",
        collection="chunks",
        is_ready=is_ready,
        indexes_active=active_indexes
    )
