"""Exam-room heartbeat (ported from the VN center realtime board, student side only).

While a student takes an exam the exam room POSTs a lightweight heartbeat. The global
stack has no teacher/center board, so the only consumer is the submit path:
`app/utils/exam_progress.tab_switches_for` reads the `tab_switches` of the ONE
`exam_progress` row per user (upserted here) and stores it on the ExamResult.

Best-effort by design: these endpoints never raise on a DB hiccup, so they can never
disrupt an exam.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import User, ExamProgress
from app.routes.admin.auth import get_current_student
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()


def _now():
    return get_vietnam_time().replace(tzinfo=None)


class Heartbeat(BaseModel):
    exam_id: Optional[int] = None
    skill: Optional[str] = None            # listening/reading/writing/speaking
    title: Optional[str] = None            # e.g. "Part 1: Chicken"
    questions_done: Optional[int] = 0
    total_questions: Optional[int] = None
    last_question: Optional[int] = None
    part: Optional[int] = None             # current part (1-4); accepted, not stored
    tab_switches: Optional[int] = 0        # times the student left the exam tab this attempt
    word_count: Optional[int] = None       # writing only; accepted, not stored


@router.post("/exam/heartbeat")
async def exam_heartbeat(
    payload: Heartbeat,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_student),
):
    """Upsert the student's single ExamProgress row."""
    now = _now()
    try:
        row = db.query(ExamProgress).filter(ExamProgress.user_id == current.user_id).first()
        if not row:
            row = ExamProgress(user_id=current.user_id)
            db.add(row)
        # A new exam (or a restart after /stop) starts a fresh attempt.
        if row.exam_id != payload.exam_id or not row.is_active:
            row.started_at = now
        row.exam_id = payload.exam_id
        row.skill = (payload.skill or None) and payload.skill[:30]
        row.title = (payload.title or None) and payload.title[:255]
        row.questions_done = payload.questions_done or 0
        row.total_questions = payload.total_questions
        row.last_question = payload.last_question
        row.tab_switches = max(0, payload.tab_switches or 0)
        row.is_active = True
        row.updated_at = now
        db.commit()
    except Exception:
        db.rollback()
        return {"tracked": False}
    return {"tracked": True}


@router.post("/exam/heartbeat/stop")
async def exam_heartbeat_stop(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_student),
):
    """Mark the student as no longer in an exam (called on submit/leave). The row is
    kept (not deleted) so a submit racing the stop call still finds its tab count."""
    try:
        row = db.query(ExamProgress).filter(ExamProgress.user_id == current.user_id).first()
        if row:
            row.is_active = False
            row.updated_at = _now()
            db.commit()
    except Exception:
        db.rollback()
    return {"ok": True}
