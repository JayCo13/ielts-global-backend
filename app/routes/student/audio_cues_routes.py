"""Per-question audio cues (ported from the VN tree) for the listening review screen.

Lets a student replay just the moment their answer was spoken, instead of hunting
through the recording. Built from `listening_alignments`; see app/utils/audio_cues.py.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import ExamSection, User
from app.routes.admin.auth import get_current_student
from app.utils.audio_cues import build_cues

router = APIRouter()


@router.get("/listening/exam/{exam_id}/part/{part_number}/audio-cues", response_model=dict)
async def get_audio_cues(
    exam_id: int,
    part_number: int,
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.order_number == part_number,
        ExamSection.section_type == 'listening',
    ).first()
    if not section:
        raise HTTPException(status_code=404, detail="Listening part not found")

    cues = build_cues(db, section.section_id)
    return {
        "section_id": section.section_id,
        "available": bool(cues),
        # Keyed by question_id so the review screen can look a cue up directly.
        "cues": {str(c["question_id"]): c for c in cues if c["start"] is not None},
    }
