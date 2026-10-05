"""Gate for reading the CONTENT of an exam.

Ported from the Vietnam tree (ielts-main-nov). Previously the endpoints that
return the exam itself never checked access:

    GET /student/exam/{exam_id}/start      → sections + questions + options
    GET /student/reading-test/{exam_id}    → passages + questions

so any logged-in account could read every exam just by knowing its id.

The "already submitted" exception is deliberate: a student whose VIP has expired
must still be able to review the tests they took.
"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.models import ExamResult, User

VIP_REQUIRED_MSG = "This test is only available to VIP members."


async def require_exam_access(db: Session, user: User, exam_id: int) -> None:
    """Raise 403 if `user` may not view the content of exam `exam_id`."""
    # Lazy import: app.routes.admin.auth pulls in the whole route tree.
    from app.routes.admin.auth import check_exam_access

    if await check_exam_access(user, exam_id, db):
        return

    # Already submitted this exam → allow review, even after VIP expiry.
    already_done = db.query(ExamResult.result_id).filter(
        ExamResult.user_id == user.user_id,
        ExamResult.exam_id == exam_id).first()
    if already_done:
        return

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=VIP_REQUIRED_MSG)
