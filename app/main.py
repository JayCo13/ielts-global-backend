from dotenv import load_dotenv
import os

# Load environment variables first
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.routes import router as api_router
from app.database import engine
from fastapi.staticfiles import StaticFiles
from app.utils.redis_cache import cache
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    """Initialize Redis + pre-warm DB pool + PayPal token on startup"""
    await cache.connect()
    # Pre-warm DB connection pool — establish connections eagerly
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection pool pre-warmed successfully")
    except Exception as e:
        logger.warning(f"DB pre-warm failed (will retry on first request): {e}")
    # Pre-warm PayPal OAuth token so first payment is fast
    try:
        from app.utils.paypal_service import get_access_token
        get_access_token()
        logger.info("PayPal access token pre-warmed successfully")
    except Exception as e:
        logger.warning(f"PayPal token pre-warm failed: {e}")
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

# Lightweight health check — no DB, instant response for keep-alive pings
@app.get("/health")
def health_check():
    return JSONResponse(content={"status": "ok"}, status_code=200)

# Warmup endpoint — warms DB + Redis connections
@app.get("/warmup")
def warmup():
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    return JSONResponse(content={"status": "warm", "db": db_ok}, status_code=200)

# PayPal warmup — pre-cache OAuth token so payment is instant
@app.get("/warmup-paypal")
def warmup_paypal():
    try:
        from app.utils.paypal_service import get_access_token
        get_access_token()
        return JSONResponse(content={"status": "ok", "paypal": True}, status_code=200)
    except Exception as e:
        logger.warning(f"PayPal warmup failed: {e}")
        return JSONResponse(content={"status": "ok", "paypal": False}, status_code=200)

# Include all routes
app.include_router(api_router)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def read_root():
    return {"message": "Welcome to the IELTS Practice API"}
