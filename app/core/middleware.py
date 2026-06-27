"""
PCS Business OS - Custom Exceptions, Global Error Handlers, and Telemetry Middleware
File: /app/core/middleware.py
"""

import logging
import time
import uuid
from typing import Any, Dict, Optional
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("pcs-business-os.middleware")


# =====================================================================
# 1. Domain-Specific Custom Exceptions
# =====================================================================

class PCSBusinessException(Exception):
    """Base exception for all domain-specific business logic errors."""
    def __init__(
        self, 
        message: str, 
        error_code: str = "BUSINESS_RULE_VIOLATION", 
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[Any] = None
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details


class EntityNotFoundException(PCSBusinessException):
    """Exception raised when a requested resource is missing in the database."""
    def __init__(self, message: str, details: Optional[Any] = None):
        super().__init__(
            message=message,
            error_code="RESOURCE_NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
            details=details
        )


class InsufficientPermissionsException(PCSBusinessException):
    """Exception raised during authorization failures (RBAC policies)."""
    def __init__(self, message: str = "You do not have permission to execute this operation."):
        super().__init__(
            message=message,
            error_code="INSUFFICIENT_PERMISSIONS",
            status_code=status.HTTP_403_FORBIDDEN
        )


class InvalidCredentialsException(PCSBusinessException):
    """Exception raised when credential validation fails (Auth token / password)."""
    def __init__(self, message: str = "Invalid authentication credentials provided."):
        super().__init__(
            message=message,
            error_code="INVALID_CREDENTIALS",
            status_code=status.HTTP_401_UNAUTHORIZED
        )


class DatabaseTimeoutException(PCSBusinessException):
    """Exception raised when MongoDB queries exceed connection bounds."""
    def __init__(self, message: str = "Database operation timed out. Please try again later."):
        super().__init__(
            message=message,
            error_code="DATABASE_TIMEOUT",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE
        )


# =====================================================================
# 2. Structured JSON Error Response Schema
# =====================================================================

def create_error_response(
    message: str,
    error_code: str,
    status_code: int,
    request_id: Optional[str] = None,
    details: Optional[Any] = None
) -> JSONResponse:
    """Consistently formats unified JSON error bodies across the entire backend."""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "message": message,
                "code": error_code,
                "status_code": status_code,
                "details": details
            },
            "request_id": request_id
        }
    )


# =====================================================================
# 3. Global Exception Registration Subsystem
# =====================================================================

def register_exception_handlers(app: FastAPI):
    """
    Binds high-fidelity custom exception handlers to the FastAPI app,
    ensuring unhandled bugs, validation faults, and domain errors output identical schemas.
    """

    @app.exception_handler(PCSBusinessException)
    async def pcs_business_exception_handler(request: Request, exc: PCSBusinessException):
        request_id = getattr(request.state, "request_id", None)
        logger.warning(
            f"[{request_id}] Business logic failure on {request.method} {request.url.path}: "
            f"code={exc.error_code} msg={exc.message}"
        )
        return create_error_response(
            message=exc.message,
            error_code=exc.error_code,
            status_code=exc.status_code,
            request_id=request_id,
            details=exc.details
        )

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
        request_id = getattr(request.state, "request_id", None)
        # Map status codes to helpful, clean codes
        error_code = f"HTTP_{exc.status_code}"
        if exc.status_code == 404:
            error_code = "ENDPOINT_NOT_FOUND"
        elif exc.status_code == 401:
            error_code = "UNAUTHORIZED_ACCESS"
        elif exc.status_code == 403:
            error_code = "FORBIDDEN_ACCESS"

        logger.warning(
            f"[{request_id}] HTTP Exception {exc.status_code} at {request.url.path}: {exc.detail}"
        )
        return create_error_response(
            message=exc.detail,
            error_code=error_code,
            status_code=exc.status_code,
            request_id=request_id
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", None)
        
        # Format the validation issues cleanly
        formatted_errors = []
        for error in exc.errors():
            formatted_errors.append({
                "loc": " -> ".join(str(l) for l in error.get("loc", [])),
                "msg": error.get("msg", "Validation error"),
                "type": error.get("type", "value_error")
            })

        logger.warning(
            f"[{request_id}] Schema validation failure on {request.method} {request.url.path}: {formatted_errors}"
        )
        return create_error_response(
            message="The payload format or parameter constraints are invalid.",
            error_code="VALIDATION_FAILED",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            request_id=request_id,
            details=formatted_errors
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)
        logger.critical(
            f"[{request_id}] Unhandled Exception on {request.method} {request.url.path}: {str(exc)}",
            exc_info=True
        )
        
        # Mask exception details in production environments to prevent sensitive leakages
        env = os.getenv("ENV", "production").lower()
        message = (
            "An unexpected internal server error occurred. Please contact customer support "
            "and supply the associated Request ID."
        )
        details = None
        if env == "development":
            message = str(exc)
            details = {
                "exception_type": type(exc).__name__,
                "arguments": getattr(exc, "args", [])
            }

        return create_error_response(
            message=message,
            error_code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_id=request_id,
            details=details
        )


# =====================================================================
# 4. Correlation and Telemetry Performance Middleware
# =====================================================================

class CorrelationAndTelemetryMiddleware(BaseHTTPMiddleware):
    """
    Intercepts incoming requests to:
    1. Extract or auto-generate a unique 'X-Request-ID' correlation tracking header.
    2. Attach trace fields to request.state variables.
    3. Measure precise execution durations, writing standardized response headers.
    4. Log consistent metrics on completed/aborted connection events.
    """
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # 1. Retrieve or generate unique correlation ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        
        # Bind the tracking ID to request state
        request.state.request_id = request_id
        
        # 2. Extract telemetry context metadata
        client_host = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("User-Agent", "unknown")
        
        logger.debug(
            f"[{request_id}] Incoming {request.method} {request.url.path} "
            f"from host {client_host} ({user_agent})"
        )

        try:
            # 3. Process downstream routers and pipeline steps
            response = await call_next(request)
            
            # 4. Calculate timing and append headers
            duration = time.time() - start_time
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time"] = f"{duration:.4f}s"
            
            # Write audit logs with timing
            logger.info(
                f"[{request_id}] {request.method} {request.url.path} -> "
                f"Status: {response.status_code} | Duration: {duration:.4f}s",
                extra={
                    "request_id": request_id,
                    "process_time": duration,
                    "client_ip": client_host
                }
            )
            return response

        except Exception as err:
            # Fallback wrapper to guarantee tracing even if lower steps abort dramatically
            duration = time.time() - start_time
            logger.error(
                f"[{request_id}] Aborted {request.method} {request.url.path} "
                f"due to crash after {duration:.4f}s: {str(err)}",
                exc_info=True
            )
            raise err
