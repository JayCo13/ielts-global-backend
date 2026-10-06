"""Admin: manage auto-forecast Occurrence Count per part + decay settings."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models.models import ExamSection, WritingTask, Exam
from app.routes.admin.auth import get_current_admin
from app.jobs.forecast_auto import (
    apply_occurrence_change, recompute_levels, run_decay, get_decay_days, set_decay_days,
)

router = APIRouter()


def _iso(dt):
    return dt.isoformat() if dt else None


@router.get("/forecast-auto/parts")
async def list_parts(
    q: Optional[str] = None,
    only_active: bool = False,   # only occurrence_count >= 1
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """All full-test parts, sorted by occurrence_count desc."""
    exam_titles = {e.exam_id: e.title for e in db.query(Exam.exam_id, Exam.title).all()}

    rows = []
    secs = (
        db.query(ExamSection)
        .filter(ExamSection.section_type.in_(['reading', 'listening']))
        .all()
    )
    for s in secs:
        rows.append({
            "kind": "section",
            "id": s.section_id,
            "test": exam_titles.get(s.exam_id, f"Exam {s.exam_id}"),
            "exam_id": s.exam_id,
            "skill": s.section_type,
            "part": s.order_number,
            "title": s.part_title or s.forecast_title or "",
            "is_forecast": bool(s.is_forecast),
            "occurrence_count": s.occurrence_count or 0,
            "forecast_level": s.forecast_level,
            "last_updated": _iso(s.forecast_last_updated),
        })
    tasks = db.query(WritingTask).all()
    for t in tasks:
        rows.append({
            "kind": "task",
            "id": t.task_id,
            "test": exam_titles.get(t.test_id, f"Exam {t.test_id}"),
            "exam_id": t.test_id,
            "skill": "writing",
            "part": t.part_number,
            "title": t.title or "",
            "occurrence_count": t.occurrence_count or 0,
            "forecast_level": t.forecast_level,
            "last_updated": _iso(t.forecast_last_updated),
        })

    if only_active:
        rows = [r for r in rows if r["occurrence_count"] >= 1]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r["test"] or "").lower() or ql in (r["title"] or "").lower()]

    rows.sort(key=lambda r: (-r["occurrence_count"], (r["test"] or ""), r["part"] or 0))
    return {"parts": rows, "decay_days": get_decay_days(db)}


class OccurrenceUpdate(BaseModel):
    kind: str          # 'section' | 'task'
    id: int
    occurrence_count: int


@router.put("/forecast-auto/part")
async def update_occurrence(
    payload: OccurrenceUpdate,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    if payload.kind == "section":
        part = db.query(ExamSection).filter(ExamSection.section_id == payload.id).first()
    elif payload.kind == "task":
        part = db.query(WritingTask).filter(WritingTask.task_id == payload.id).first()
    else:
        raise HTTPException(status_code=400, detail="kind must be 'section' or 'task'")
    if not part:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Part not found")

    apply_occurrence_change(db, part, payload.occurrence_count)
    return {
        "success": True,
        "occurrence_count": part.occurrence_count,
        "forecast_level": part.forecast_level,
        "last_updated": _iso(part.forecast_last_updated),
    }


class DecaySettings(BaseModel):
    decay_days: int


@router.get("/forecast-auto/settings")
async def get_settings(db: Session = Depends(get_db), current_admin=Depends(get_current_admin)):
    return {"decay_days": get_decay_days(db)}


@router.put("/forecast-auto/settings")
async def put_settings(
    payload: DecaySettings,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    return {"decay_days": set_decay_days(db, payload.decay_days)}


@router.post("/forecast-auto/recompute-levels")
async def recompute(db: Session = Depends(get_db), current_admin=Depends(get_current_admin)):
    recompute_levels(db)
    db.commit()
    return {"success": True}


@router.post("/forecast-auto/run-decay")
async def trigger_decay(db: Session = Depends(get_db), current_admin=Depends(get_current_admin)):
    return {"success": True, **run_decay(db)}
