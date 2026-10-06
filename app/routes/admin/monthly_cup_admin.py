"""Admin: trigger the Monthly Cup month-end snapshot (VN runs it by cron on day 1).

Global runs on Koyeb with no cron, so these admin-protected endpoints are the
triggers. Both are idempotent per (skill, year, month): an already-frozen month is
reported as "exists" and left untouched.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.admin.auth import get_current_admin
from app.jobs.monthly_cup import snapshot_month, backfill, _prev_month
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()


@router.post("/monthly-cup/snapshot")
async def trigger_monthly_cup_snapshot(
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Freeze the Top 3 per skill. Defaults to the PREVIOUS (completed) month."""
    if (year is None) != (month is None):
        raise HTTPException(status_code=400, detail="Pass both year and month, or neither")
    if year is None:
        year, month = _prev_month()
    if not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="month must be 1-12")
    # Freezing a month that is still running would lock in partial standings forever
    # (snapshots are idempotent and never rewritten), so only completed months.
    now = get_vietnam_time().replace(tzinfo=None)
    if (year, month) >= (now.year, now.month):
        raise HTTPException(status_code=400, detail="Only completed months can be snapshotted")
    return {"success": True, "year": year, "month": month, "result": snapshot_month(db, year, month)}


@router.post("/monthly-cup/backfill")
async def trigger_monthly_cup_backfill(
    months: int = 12,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Snapshot the last `months` completed months (idempotent)."""
    months = max(1, min(int(months), 36))
    return {"success": True, "months": backfill(db, months)}
