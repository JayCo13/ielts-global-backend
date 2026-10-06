from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional
from app.database import get_db
from app.models.models import ErrorReport, User
from app.routes.admin.auth import get_current_admin
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()


def _serialize(r: ErrorReport, username: Optional[str], email: Optional[str]):
    return {
        "report_id": r.report_id,
        "user_id": r.user_id,
        "username": username,
        "email": email,
        "exam_id": r.exam_id,
        "result_id": r.result_id,
        "skill": r.skill,
        "exam_title": r.exam_title,
        "error_types": r.error_types or [],
        "wrong_answer_questions": r.wrong_answer_questions,
        "mis_graded_questions": r.mis_graded_questions,
        "description": r.description,
        "is_viewed": bool(r.is_viewed),
        "viewed_at": r.viewed_at.isoformat() if r.viewed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/error-reports")
async def list_error_reports(
    is_viewed: Optional[bool] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """List customer error reports, newest first. Filter by is_viewed."""
    query = db.query(ErrorReport)
    if is_viewed is not None:
        query = query.filter(ErrorReport.is_viewed == is_viewed)

    total = query.count()
    unviewed = db.query(ErrorReport).filter(ErrorReport.is_viewed == False).count()  # noqa: E712

    reports = (
        query.order_by(desc(ErrorReport.created_at))
        .offset(skip)
        .limit(min(limit, 200))
        .all()
    )

    # Resolve reporter info in one pass.
    user_ids = {r.user_id for r in reports if r.user_id}
    users = {}
    if user_ids:
        for u in db.query(User).filter(User.user_id.in_(user_ids)).all():
            users[u.user_id] = u

    items = [
        _serialize(
            r,
            users.get(r.user_id).username if users.get(r.user_id) else None,
            users.get(r.user_id).email if users.get(r.user_id) else None,
        )
        for r in reports
    ]

    return {"total": total, "unviewed": unviewed, "items": items}


@router.get("/error-reports/unviewed-count")
async def unviewed_count(
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    count = db.query(ErrorReport).filter(ErrorReport.is_viewed == False).count()  # noqa: E712
    return {"unviewed": count}


@router.patch("/error-reports/{report_id}/viewed")
async def set_viewed(
    report_id: int,
    is_viewed: bool = True,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Mark a report as viewed / unviewed."""
    report = db.query(ErrorReport).filter(ErrorReport.report_id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    report.is_viewed = is_viewed
    report.viewed_at = get_vietnam_time().replace(tzinfo=None) if is_viewed else None
    db.commit()
    return {"success": True, "report_id": report_id, "is_viewed": is_viewed}


@router.delete("/error-reports/{report_id}")
async def delete_error_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    report = db.query(ErrorReport).filter(ErrorReport.report_id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    db.delete(report)
    db.commit()
    return {"success": True}
