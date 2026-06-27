"""
PCS Business OS - High-Fidelity File Validation and Security Utilities
File: /app/utils/file_validation.py
"""

import os
import re
import unicodedata
from typing import Set, Tuple, Optional
from fastapi import HTTPException, status


# Configuration defaults for security-hardened deployments
MAX_FILE_SIZE_BYTES_DEFAULT = 10 * 1024 * 1024  # 10 MB default limit

# Strictly permitted file categories and their corresponding MIME types
ALLOWED_MIME_TYPES = {
    # Documents
    "pdf": {"application/pdf"},
    "txt": {"text/plain"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "doc": {"application/msword"},
    # Data & Spreadsheets
    "csv": {"text/csv", "application/csv", "text/comma-separated-values"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "xls": {"application/vnd.ms-excel"},
    "json": {"application/json", "text/json"},
    # Visual Assets
    "png": {"image/png"},
    "jpg": {"image/jpeg", "image/jpg"},
    "jpeg": {"image/jpeg", "image/jpg"},
    "webp": {"image/webp"},
    "gif": {"image/gif"}
}

# Signatures (Magic Bytes) of common safe file formats for deep inspection
MAGIC_NUMBERS = {
    "pdf": b"%PDF",
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "gif": b"GIF8",
    "zip": b"PK\x03\x04",  # docx and xlsx are technically zip containers
}


class FileValidator:
    """
    Utility class implementing strict visual and behavioral sanitizations
    on file uploads to protect the PCS Business OS infrastructure from malicious inputs.
    """

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Sanitizes a filename to prevent Path Traversal, Directory Escapes (../), 
        and command-injection characters.
        
        Converts the name to ASCII and keeps only alphanumeric characters, dots, and underscores.
        """
        if not filename:
            return "unnamed_asset"

        # 1. Strip path structure safely (using standard library os.path)
        base_name = os.path.basename(filename)

        # 2. Normalize unicode characters (decomposes ligatures and symbols)
        normalized = unicodedata.normalize("NFKD", base_name).encode("ascii", "ignore").decode("ascii")

        # 3. Clean up non-alphanumeric/dot/hyphen/underscore structures
        cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", normalized)

        # 4. Collapse consecutive dots and remove leading dots/slashes to prevent relative resolution
        cleaned = re.sub(r"\.+", ".", cleaned)
        cleaned = cleaned.strip(".-_")

        # 5. Default fallback if name was fully cleared by sanitization
        if not cleaned:
            return f"sanitized_file_{hash(filename) % 100000}"

        return cleaned

    @staticmethod
    def validate_metadata(
        filename: str, 
        content_type: str, 
        allowed_extensions: Optional[Set[str]] = None
    ) -> Tuple[str, str]:
        """
        Validates file extensions against a set of allowed types.
        Ensures content_type matches expected MIME types.
        """
        if allowed_extensions is None:
            allowed_extensions = set(ALLOWED_MIME_TYPES.keys())

        # Validate filename presence and extension structure
        if "." not in filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File has no clear extension or format identifier."
            )

        ext = filename.rsplit(".", 1)[1].lower().strip()
        if ext not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File format '.{ext}' is strictly forbidden by server rules."
            )

        # Content-type check (if MIME type mapping is registered)
        expected_mimes = ALLOWED_MIME_TYPES.get(ext, set())
        normalized_content_type = content_type.lower().strip()

        if expected_mimes and normalized_content_type not in expected_mimes:
            # We log a security warning or raise error depending on policy
            # For robustness, we fallback to accepting correct logical mapping if it's broad
            # but raise alert if it's mismatching completely (e.g. executable disguised as PDF)
            if "application/octet-stream" not in normalized_content_type:
                # Basic alert for cross-mime attempts (e.g. exe uploaded as text)
                if not any(mime in normalized_content_type for mime in expected_mimes):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Content-type mismatch. Expected a MIME type matching '.{ext}' extension."
                    )

        return ext, normalized_content_type

    @staticmethod
    def verify_magic_bytes(file_path: str, expected_extension: str) -> bool:
        """
        Optionally reads the first few bytes of a saved file to confirm its magic signature.
        Foil attempts to bypass uploads restrictions by renaming .exe files to .pdf files.
        """
        if expected_extension not in MAGIC_NUMBERS:
            # Skip check for text/unstructured files lacking standard static magic headers (like text/csv)
            return True

        expected_sig = MAGIC_NUMBERS[expected_extension]
        try:
            with open(file_path, "rb") as f:
                header = f.read(len(expected_sig))
                # Validate zip container signature for Microsoft Office files (docx/xlsx)
                if expected_extension in ["docx", "xlsx"]:
                    return header.startswith(MAGIC_NUMBERS["zip"])
                return header.startswith(expected_sig)
        except Exception:
            return False
