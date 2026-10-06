"""Admin: manually trigger the Top Performer recompute (VN runs it by daily cron; global has no cron, so this endpoint is the trigger)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.routes.admin.auth import get_current_admin
from app.jobs.top_performers import recompute_top_performers

router = APIRouter()


@router.post("/top-performers/recompute")
async def trigger_top_performers(
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    return {"success": True, **recompute_top_performers(db)}
