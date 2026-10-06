"""Purge of expired student "Custom Tasks" (self-added Writing essays). Ported from VN.

Retention is 24h and the UI warns students to download PDF/Word before that. The
delete also runs lazily in `GET /student/writing/custom` for the student who opens
the page; this job applies the same rule to every student, so essays from students
who never come back do not pile up.

A custom task is an Exam whose title carries CUSTOM_PREFIX (stamped by
`POST /student/writing/custom`), with created_by set and is_active = False.
Matching on created_by + is_active alone is NOT enough: an admin-deactivated real
exam looks identical on those two columns.

Global runs on Koyeb with no cron — trigger it from the admin endpoint
POST /admin/writing-custom/purge (app/routes/admin/writing_custom_admin.py), or:
    python -m app.jobs.purge_custom_writing
"""
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models.models import Exam, WritingTask, WritingAnswer, WritingAttempt, ExamAccessType
from app.utils.datetime_utils import get_vietnam_time

RETENTION_HOURS = 24
CUSTOM_PREFIX = "[Custom] "   # must match app/routes/student/writing_custom.py

_IMG_SRC = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def _delete_task_images(instructions: str) -> None:
    """Best-effort removal of the custom task's uploaded image(s) from R2. Never raises;
    URLs that are not ours (or local /static fallbacks) are ignored."""
    if not instructions:
        return
    try:
        from app.utils.r2_storage import delete_object_from_r2
        for src in _IMG_SRC.findall(instructions):
            if "/images/writing_custom/" in src:
                delete_object_from_r2(src)
    except Exception:   # noqa: BLE001 — storage cleanup must not block the DB purge
        pass


def delete_custom_exam(db: Session, exam: Exam) -> None:
    """Delete one custom task exam and everything hanging off it (no commit)."""
    # exam_access_types keys on exam_id, so it has to go before the exam or the delete
    # aborts (the VN lazy cleanup silently stopped working because of this).
    db.query(ExamAccessType).filter(ExamAccessType.exam_id == exam.exam_id).delete()
    for task in db.query(WritingTask).filter(WritingTask.test_id == exam.exam_id).all():
        _delete_task_images(task.instructions)
        db.query(WritingAnswer).filter(WritingAnswer.task_id == task.task_id).delete()
        # Retake snapshots reference the task by FK too.
        db.query(WritingAttempt).filter(WritingAttempt.task_id == task.task_id).delete()
        db.delete(task)
    db.delete(exam)


def run_purge(db: Session, user_id: int = None) -> dict:
    """Delete every custom task older than the retention window (optionally only for
    one student — used by the lazy cleanup on the student's own list)."""
    cutoff = get_vietnam_time().replace(tzinfo=None) - timedelta(hours=RETENTION_HOURS)

    q = db.query(Exam).filter(
        Exam.created_by.isnot(None),
        Exam.is_active == False,  # noqa: E712
        Exam.title.like(f"{CUSTOM_PREFIX}%"),
        Exam.created_at < cutoff,
    )
    if user_id is not None:
        q = q.filter(Exam.created_by == user_id)

    deleted = 0
    failed = []
    students = set()
    for exam in q.all():
        try:
            students.add(exam.created_by)
            delete_custom_exam(db, exam)
            db.commit()   # per essay: one stubborn row can't block all the others
            deleted += 1
        except Exception as exc:   # noqa: BLE001 - keep going, report at the end
            db.rollback()
            failed.append((exam.exam_id, str(exc)[:120]))

    return {
        "deleted_essays": deleted,
        "students_affected": len(students),
        "failed": failed,
        "retention_hours": RETENTION_HOURS,
    }


def main():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        print("Purge custom writing:", run_purge(db))
    finally:
        db.close()


if __name__ == "__main__":
    main()
