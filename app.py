"""
PCS Business OS - FastAPI Main Application Entrypoint
File: /app.py
"""

import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
from app.core.logging_config import configure_system_logging
configure_system_logging()
logger = logging.getLogger("pcs-business-os")

# MongoDB connection placeholder for app state
mongodb_client: AsyncIOMotorClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for handling startup and shutdown events.
    Enables production-ready, clean connection management for MongoDB.
    """
    global mongodb_client
    # Retrieve configuration
    mongo_uri = os.getenv("MONGO_URI")
    db_name = os.getenv("MONGO_DB_NAME", "pcs_business_os")
    
    logger.info("Initializing PCS Business OS Services...")
    
    # Initialize MongoDB Client
    if mongo_uri:
        try:
            logger.info("Connecting to MongoDB Atlas...")
            mongodb_client = AsyncIOMotorClient(mongo_uri)
            # Verify connection
            await mongodb_client.admin.command('ping')
            app.state.mongodb = mongodb_client
            app.state.db = mongodb_client[db_name]
            logger.info(f"Successfully connected to MongoDB database: {db_name}")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB Atlas: {str(e)}")
            # In production, we might want to fail-fast, but let's allow starting with warning for development
            app.state.mongodb = None
            app.state.db = None
    else:
        logger.warning("MONGO_URI not configured. Database-dependent endpoints will be unavailable.")
        app.state.mongodb = None
        app.state.db = None

    # Initialize Gemini SDK environment verification
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        logger.warning("GEMINI_API_KEY is not set. AI & RAG capabilities will be limited.")
    else:
        logger.info("Gemini API key verification passed.")

    yield
    
    # Shutdown logic
    logger.info("Shutting down PCS Business OS Services...")
    if mongodb_client:
        logger.info("Closing MongoDB connection pool...")
        mongodb_client.close()
    logger.info("Application shutdown complete.")

# Instantiate FastAPI App
app = FastAPI(
    title=os.getenv("PROJECT_NAME", "PCS Business OS"),
    description="Production-grade, asynchronous FastAPI backend powering PCS Business OS with MongoDB Atlas & Gemini AI.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan
)

# Configure CORS Middleware
# Restrict or configure origins depending on the APP_URL environment setting
allowed_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
]
app_url = os.getenv("APP_URL")
if app_url:
    allowed_origins.append(app_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.core.middleware import CorrelationAndTelemetryMiddleware, register_exception_handlers

# Register Global Error Exception Handlers
register_exception_handlers(app)

# Register Performance and Correlation Telemetry Middleware
app.add_middleware(CorrelationAndTelemetryMiddleware)

# Root Endpoint
@app.get("/", tags=["Root"])
async def root():
    return {
        "app": "PCS Business OS Backend",
        "version": "1.0.0",
        "status": "online",
        "docs_url": "/docs"
    }

# Liveness and Database Health Endpoints
@app.get("/health", tags=["Health"])
async def health_check(request: Request):
    db_status = "unconfigured"
    if request.app.state.mongodb:
        try:
            await request.app.state.mongodb.admin.command('ping')
            db_status = "healthy"
        except Exception as e:
            db_status = f"unhealthy: {str(e)}"
            
    return {
        "status": "healthy" if db_status in ["healthy", "unconfigured"] else "degraded",
        "timestamp": time.time(),
        "database": db_status,
        "environment": os.getenv("ENV", "development")
    }

# Router inclusions will be registered here sequentially
from app.routes.auth import router as auth_router
from app.routes.users import router as users_router
from app.routes.upload import router as upload_router
from app.routes.rag import router as rag_router
from app.routes.admin import router as admin_router

app.include_router(auth_router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(users_router, prefix="/api/v1/users", tags=["Users"])
app.include_router(upload_router, prefix="/api/v1/files", tags=["Files"])
app.include_router(rag_router, prefix="/api/v1/rag", tags=["RAG"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["Administration"])
