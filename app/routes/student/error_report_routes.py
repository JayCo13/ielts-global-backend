from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from app.database import get_db
from app.models.models import ErrorReport, Exam, User
from app.routes.admin.auth import get_current_student

router = APIRouter()

# Canonical set of error-type keys, shared with the frontend report form.
VALID_ERROR_TYPES = {
    "wrong_answer",   # Wrong answer key
    "mis_graded",     # Graded incorrectly
    "spelling",       # Spelling mistake
    "audio",          # Audio problem
    "audio_cue",      # Replay jumps to the wrong spot (auto-aligned cue is off)
    "ui",             # Display / UI problem
    "other",          # Other
    # Speaking. The ErrorReport table is shared by all 4 skills, so only keys are
    # added, no schema change. For Speaking, `exam_id` is empty (there is no row in
    # exams), `exam_title` snapshots the question text for the admin, and
    # `wrong_answer_questions` carries the reported question_id.
    "question_problem",       # Problem with the question
    "score_fluency",          # Fluency & Coherence score is off
    "score_lexical",          # Lexical Resource score is off
    "score_grammar",          # Grammar score is off
    "score_pronunciation",    # Pronunciation score is off
    "wrong_error_detection",  # AI flagged errors incorrectly
    "wrong_feedback",         # Feedback is incorrect
}


class ErrorReportCreate(BaseModel):
    exam_id: Optional[int] = None
    result_id: Optional[int] = None
    skill: Optional[str] = None
    exam_title: Optional[str] = None
    error_types: List[str] = []
    wrong_answer_questions: Optional[str] = None
    mis_graded_questions: Optional[str] = None
    description: Optional[str] = None


@router.post("/error-report")
async def create_error_report(
    report: ErrorReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student),
):
    """File an error report from the exam Review screen."""
    types = [t for t in (report.error_types or []) if t in VALID_ERROR_TYPES]
    if not types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please select at least one error type.",
        )

    # Snapshot the exam title so the admin list stays readable even if the exam
    # is later renamed/deleted.
    exam_title = report.exam_title
    if not exam_title and report.exam_id:
        exam = db.query(Exam).filter(Exam.exam_id == report.exam_id).first()
        if exam:
            exam_title = exam.title

    new_report = ErrorReport(
        user_id=current_user.user_id,
        exam_id=report.exam_id,
        result_id=report.result_id,
        skill=report.skill,
        exam_title=exam_title,
        error_types=types,
        wrong_answer_questions=report.wrong_answer_questions,
        mis_graded_questions=report.mis_graded_questions,
        description=report.description,
        is_viewed=False,
    )
    db.add(new_report)
    db.commit()
    db.refresh(new_report)

    return {"success": True, "report_id": new_report.report_id}
