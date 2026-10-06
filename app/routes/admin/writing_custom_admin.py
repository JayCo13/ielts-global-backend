"""Admin: trigger the purge of expired student Custom Writing Tasks (VN runs it by cron).

Global runs on Koyeb with no scheduler, so this admin-protected endpoint is the
trigger. Idempotent: only tasks older than the 24h retention window are removed.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.admin.auth import get_current_admin
from app.jobs.purge_custom_writing import run_purge

router = APIRouter()


@router.post("/writing-custom/purge")
async def trigger_custom_writing_purge(
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    return {"success": True, "result": run_purge(db)}
