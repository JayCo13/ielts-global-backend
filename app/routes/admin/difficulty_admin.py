"""Admin: manually trigger the difficulty recompute (VN runs it by daily cron; global has no cron, so this endpoint is the trigger)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.routes.admin.auth import get_current_admin
from app.jobs.recompute_difficulty import recompute_difficulty

router = APIRouter()


@router.post("/difficulty/recompute")
async def trigger_recompute(
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    return {"success": True, **recompute_difficulty(db)}
