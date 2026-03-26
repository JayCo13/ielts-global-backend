from fastapi import APIRouter
from app.routes.auth.password_reset import router as password_reset_router
from app.routes.auth.email_verification import router as email_verification_router

router = APIRouter()

router.include_router(password_reset_router, prefix="/auth", tags=["authentication"])
router.include_router(email_verification_router, prefix="/auth", tags=["authentication"]) 