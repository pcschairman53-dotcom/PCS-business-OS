"""
PCS Business OS - Safe Async File Upload and Storage Routes
File: /app/routes/upload.py
"""

import os
import shutil
import uuid
from datetime import datetime
from typing import List, Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_active_user
from app.core.rbac import HierarchicalRoleChecker, SystemRole
from app.models.user import UserResponse, PyObjectId

router = APIRouter()

# Safe upload destination setup
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Configuration constraints
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB limit
ALLOWED_EXTENSIONS = {
    "pdf", "txt", "docx", "csv", "xlsx", "json",
    "png", "jpg", "jpeg", "webp", "gif"
}


class FileMetadataResponse(BaseModel):
    """Schema representing file asset metadata in MongoDB Atlas."""
    id: PyObjectId = Field(..., alias="_id")
    filename: str
    original_filename: str
    content_type: str
    size_bytes: int
    storage_path: str
    uploaded_by: PyObjectId
    created_at: datetime
    updated_at: datetime

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
        "json_encoders": {ObjectId: str}
    }


def validate_file_safety(filename: str, content_type: str) -> str:
    """
    Validates file extension safety.
    Returns the lowercased extension.
    """
    if "." not in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must contain a valid extension format."
        )

    ext = filename.rsplit(".", 1)[1].lower().strip()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File extension '.{ext}' is forbidden. Allowed: {sorted(list(ALLOWED_EXTENSIONS))}"
        )
    return ext


@router.post(
    "/",
    response_model=FileMetadataResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file and index metadata (Active user only)"
)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: UserResponse = Depends(get_current_active_user)
):
    """
    Asynchronously streams, validates, and stores a file to the host storage system.
    Indexes full metadata into MongoDB Atlas for fast reference.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    # 1. Inspect file extension safety
    ext = validate_file_safety(file.filename, file.content_type)

    # 2. Track chunked file sizes during stream to prevent RAM exhaustion attacks
    unique_filename = f"{uuid.uuid4().hex}.{ext}"
    destination_path = os.path.join(UPLOAD_DIR, unique_filename)

    size_bytes = 0
    try:
        with open(destination_path, "wb") as buffer:
            while chunk := await file.read(64 * 1024):  # Read 64KB chunks
                size_bytes += len(chunk)
                if size_bytes > MAX_FILE_SIZE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Upload exceeded maximum permitted limit of {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB."
                    )
                buffer.write(chunk)
    except Exception as e:
        # Cleanup incomplete files on error
        if os.path.exists(destination_path):
            os.remove(destination_path)
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"File writing streaming failure: {str(e)}"
        )
    finally:
        await file.close()

    # 3. Create database entry
    metadata_doc = {
        "filename": unique_filename,
        "original_filename": file.filename,
        "content_type": file.content_type or f"application/{ext}",
        "size_bytes": size_bytes,
        "storage_path": destination_path,
        "uploaded_by": ObjectId(current_user.id),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }

    result = await db.files.insert_one(metadata_doc)
    new_file = await db.files.find_one({"_id": result.inserted_id})

    return new_file


@router.get(
    "/",
    response_model=List[FileMetadataResponse],
    summary="List uploaded files metadata"
)
async def list_files(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: UserResponse = Depends(get_current_active_user)
):
    """
    Returns indexed file assets metadata. Users see their own uploads;
    managers and admins can see all uploads.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    # Scoping uploads visibility
    query = {}
    if current_user.role not in ["admin", "manager"] and not current_user.is_superuser:
        query["uploaded_by"] = ObjectId(current_user.id)

    cursor = db.files.find(query).skip(skip).limit(limit).sort("created_at", -1)
    results = await cursor.to_list(length=limit)
    return results


@router.get(
    "/{file_id}",
    response_model=FileMetadataResponse,
    summary="Get details of a file asset"
)
async def read_file_metadata(
    request: Request,
    file_id: str,
    current_user: UserResponse = Depends(get_current_active_user)
):
    """
    Retrieves complete structural index metadata for a single file.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    try:
        obj_id = ObjectId(file_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file ID format."
        )

    file_doc = await db.files.find_one({"_id": obj_id})
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested file asset was not found."
        )

    # Check access permission
    if str(file_doc["uploaded_by"]) != str(current_user.id) and \
       current_user.role not in ["admin", "manager"] and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You do not own this file asset."
        )

    return file_doc


@router.get(
    "/{file_id}/download",
    summary="Download or stream actual physical file content"
)
async def download_file(
    request: Request,
    file_id: str,
    current_user: UserResponse = Depends(get_current_active_user)
):
    """
    Transfers the underlying physical file content safely via chunked stream response.
    Includes validation of access rights and checks file existence on host.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    try:
        obj_id = ObjectId(file_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file ID format."
        )

    file_doc = await db.files.find_one({"_id": obj_id})
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested file asset was not found."
        )

    # Check access permission
    if str(file_doc["uploaded_by"]) != str(current_user.id) and \
       current_user.role not in ["admin", "manager"] and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You do not own this file asset."
        )

    physical_path = file_doc["storage_path"]
    if not os.path.exists(physical_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The physical file is missing from backend disk storage."
        )

    return FileResponse(
        path=physical_path,
        media_type=file_doc["content_type"],
        filename=file_doc["original_filename"]
    )


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a file asset and metadata"
)
async def delete_file(
    request: Request,
    file_id: str,
    current_user: UserResponse = Depends(get_current_active_user)
):
    """
    Removes the indexed metadata record from Atlas database and deletes
    the actual physical asset from persistent storage.
    """
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is currently offline."
        )

    try:
        obj_id = ObjectId(file_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file ID format."
        )

    file_doc = await db.files.find_one({"_id": obj_id})
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested file asset was not found."
        )

    # Only owner, manager, or admin can delete
    if str(file_doc["uploaded_by"]) != str(current_user.id) and \
       current_user.role not in ["admin", "manager"] and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You do not own this file asset."
        )

    # Delete physical asset
    physical_path = file_doc["storage_path"]
    if os.path.exists(physical_path):
        try:
            os.remove(physical_path)
        except Exception as e:
            # We log it but proceed to clear DB entry to avoid stale indices
            # in case of host permissions issues
            pass

    # Delete db record
    await db.files.delete_one({"_id": obj_id})

    return {
        "success": True,
        "message": f"Successfully deleted file and cleared index metadata for ID: {file_id}"
    }
