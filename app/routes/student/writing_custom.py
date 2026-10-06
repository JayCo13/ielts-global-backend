"""Student "Custom Tasks" — self-added Writing essays (ported from VN "Bài tự thêm").

A student creates their own Writing task (choose Part 1/2, title, optional prompt
text, an image for Part 1, and their essay). We materialise it as a hidden Exam
(is_active=False, created_by=<student>) + one WritingTask + the student's
WritingAnswer, so the whole grading/review pipeline (/ai/writing/grade,
/student/writing/test/{id}/answers, WritingReview screen) works unchanged.

Global adaptation: the Part 1 image is stored in Cloudflare R2 (Koyeb's disk is
ephemeral) and the absolute R2 URL is embedded in the task instructions, like the
admin upload-image endpoint. Falls back to local /static only if R2 fails.
Retention: 24h (lazy cleanup here + app/jobs/purge_custom_writing.py, triggered by
POST /admin/writing-custom/purge).
"""
import os
import logging
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models.models import User, Exam, WritingTask, WritingAnswer, ExamAccessType
from app.routes.admin.auth import get_current_student
from app.utils.datetime_utils import get_vietnam_time
from app.utils.upload_security import validate_image_bytes
from app.jobs.purge_custom_writing import CUSTOM_PREFIX, run_purge, delete_custom_exam

logger = logging.getLogger(__name__)

router = APIRouter()

CUSTOM_IMAGES_DIR = "static/writing_custom"   # fallback only (R2 is primary)
_MIME = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
         ".bmp": "image/bmp", ".webp": "image/webp"}


def _now():
    return get_vietnam_time().replace(tzinfo=None)


class CustomCreate(BaseModel):
    part_number: int
    title: str                          # serves as the prompt
    instructions: Optional[str] = ""    # optional extra description
    image_url: Optional[str] = None     # required for Part 1 (the chart/diagram)
    answer_text: str


@router.post("/writing/custom/upload-image", response_model=dict)
async def upload_custom_image(
    image: UploadFile = File(...),
    current_student: User = Depends(get_current_student),
):
    if not (image.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed.")
    content = await image.read()
    # Real magic-byte check + 5MB cap (rejects SVG/HTML posing as images).
    ext = validate_image_bytes(content)
    fname = f"{uuid4()}{ext}"
    try:
        from app.utils.r2_storage import upload_image_to_r2
        url = upload_image_to_r2(content, f"writing_custom/{fname}", content_type=_MIME.get(ext, "image/jpeg"))
        return {"image_url": url}
    except Exception as e:   # noqa: BLE001
        # Same fallback as the admin upload-image endpoint: keep the feature usable if
        # R2 is misconfigured (the file then only lives until the next redeploy).
        logger.warning("R2 upload failed for custom writing image, falling back to /static: %s", e)
        os.makedirs(CUSTOM_IMAGES_DIR, exist_ok=True)
        with open(os.path.join(CUSTOM_IMAGES_DIR, fname), "wb") as buf:
            buf.write(content)
        return {"image_url": f"/static/writing_custom/{fname}"}


def _build_instructions(payload: CustomCreate) -> str:
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = ""
    # The title IS the prompt → show it first.
    title = (payload.title or "").strip()
    if title:
        html += f"<p><strong>{esc(title)}</strong></p>"
    # Optional extra description, keep line breaks as paragraphs.
    for para in (payload.instructions or "").split("\n"):
        para = para.strip()
        if para:
            html += f"<p>{esc(para)}</p>"
    if payload.image_url:
        src = esc(payload.image_url.strip()).replace('"', "&quot;")
        html += f'<img src="{src}" alt="Task image" style="max-width:100%" />'
    return html


@router.post("/writing/custom", response_model=dict)
async def create_custom_writing(
    payload: CustomCreate,
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    part = payload.part_number
    if part not in (1, 2):
        raise HTTPException(status_code=400, detail="Please choose Part 1 or Part 2.")
    if not (payload.title or "").strip():
        raise HTTPException(status_code=400, detail="Please enter the task prompt.")
    if not (payload.answer_text or "").strip():
        raise HTTPException(status_code=400, detail="Please enter your essay.")
    if part == 1 and not (payload.image_url or "").strip():
        raise HTTPException(status_code=400, detail="Part 1 requires an image of the task (chart/diagram).")
    # Only accept URLs produced by our own upload endpoint: the grader fetches this
    # image server-side, so an arbitrary URL would let a student make us fetch anything.
    from app.utils.r2_storage import R2_PUBLIC_URL
    img = (payload.image_url or "").strip()
    if img and not (img.startswith(f"{R2_PUBLIC_URL}/images/writing_custom/")
                    or img.startswith("/static/writing_custom/")):
        raise HTTPException(status_code=400, detail="Please upload the image with the upload button.")

    now = _now()
    full_title = payload.title.strip()   # the full prompt (can be long) → kept in instructions
    exam = Exam(
        title=(CUSTOM_PREFIX + full_title)[:100],   # Exam.title is VARCHAR(100)
        created_by=current_student.user_id,
        is_active=False,
        created_at=now,
    )
    db.add(exam)
    db.flush()

    task = WritingTask(
        test_id=exam.exam_id,
        part_number=part,
        task_type="report" if part == 1 else "essay",
        title=full_title[:200],   # WritingTask.title is VARCHAR(200)
        instructions=_build_instructions(payload),
        word_limit=150 if part == 1 else 250,
        is_forecast=False,
    )
    db.add(task)
    db.flush()

    answer = WritingAnswer(
        task_id=task.task_id,
        user_id=current_student.user_id,
        answer_text=payload.answer_text,
        score=0,
        created_at=now,
        updated_at=now,
    )
    db.add(answer)
    db.commit()

    return {"test_id": exam.exam_id, "task_id": task.task_id, "part_number": part}


@router.get("/writing/custom", response_model=list)
async def list_custom_writing(
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """The current student's custom tasks (newest first)."""
    # Retention policy: auto-delete this user's custom tasks older than 24h (users are
    # warned in the UI to download PDF/Word). Lazy cleanup on each visit; the global
    # purge job covers students who never come back.
    try:
        run_purge(db, user_id=current_student.user_id)
    except Exception:   # noqa: BLE001 — never let cleanup break the page listing
        db.rollback()

    # Match on the prefix too: created_by + is_active alone also describes a real exam
    # an admin deactivated.
    exams = (
        db.query(Exam)
        .filter(
            Exam.created_by == current_student.user_id,
            Exam.is_active == False,  # noqa: E712
            Exam.title.like(f"{CUSTOM_PREFIX}%"),
        )
        .order_by(Exam.exam_id.desc())
        .all()
    )
    out = []
    for exam in exams:
        task = (
            db.query(WritingTask)
            .filter(WritingTask.test_id == exam.exam_id)
            .order_by(WritingTask.part_number)
            .first()
        )
        if not task:
            continue
        answer = (
            db.query(WritingAnswer)
            .filter(WritingAnswer.task_id == task.task_id, WritingAnswer.user_id == current_student.user_id)
            .first()
        )
        out.append({
            "test_id": exam.exam_id,
            "task_id": task.task_id,
            "part_number": task.part_number,
            "title": (exam.title or "").replace(CUSTOM_PREFIX, ""),
            "created_at": exam.created_at,
            "is_ai_evaluated": bool(answer.is_ai_evaluated) if answer else False,
            "score": answer.score if answer else None,
        })
    return out


@router.delete("/writing/custom/{test_id}", response_model=dict)
async def delete_custom_writing(
    test_id: int,
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    exam = db.query(Exam).filter(
        Exam.exam_id == test_id,
        Exam.created_by == current_student.user_id,
        Exam.is_active == False,  # noqa: E712
        Exam.title.like(f"{CUSTOM_PREFIX}%"),
    ).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Custom task not found.")
    delete_custom_exam(db, exam)
    db.commit()
    return {"message": "Custom task deleted."}
