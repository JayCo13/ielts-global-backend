from dotenv import load_dotenv
import os

# Load environment variables first
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import router as api_router
from fastapi.staticfiles import StaticFiles
from app.utils.redis_cache import cache
import logging

logger = logging.getLogger(__name__)

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    """Initialize Redis connection on startup"""
    await cache.connect()
    logger.info("Application startup completed")

@app.on_event("shutdown")
async def shutdown_event():
    """Close Redis connection on shutdown"""
    await cache.disconnect()
    logger.info("Application shutdown completed")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routes
app.include_router(api_router)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def read_root():
    return {"message": "Welcome to the IELTS Practice API"}
