"""Admin tool to assign a question type to each question of a test (PA2).

Writes to Question.question_type — the same field the review endpoint surfaces in
detailed_answers, which the student result "detailed data table" and the teacher
per-type breakdown both aggregate on.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import distinct
from pydantic import BaseModel
from typing import List, Optional
import re
from app.database import get_db
from app.models.models import Exam, ExamSection, Question, QuestionGroup, WritingTask
from app.routes.admin.auth import get_current_admin

router = APIRouter()


def _strip_html(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


@router.get("/question-typing/exams")
async def list_typable_exams(
    skill: Optional[str] = None,   # 'reading' | 'listening' | None (both)
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Exams that have reading/listening sections, for the exam picker."""
    types = ['reading', 'listening']
    if skill in types:
        types = [skill]

    exam_ids = [
        row[0]
        for row in db.query(distinct(ExamSection.exam_id))
        .filter(ExamSection.section_type.in_(types))
        .all()
    ]
    if not exam_ids:
        return {"exams": []}

    exams = (
        db.query(Exam)
        .filter(Exam.exam_id.in_(exam_ids))
        .order_by(Exam.exam_id.desc())
        .all()
    )
    # Determine each exam's skill(s) in one pass.
    skill_map = {}
    for eid, stype in (
        db.query(ExamSection.exam_id, ExamSection.section_type)
        .filter(ExamSection.exam_id.in_(exam_ids))
        .all()
    ):
        skill_map.setdefault(eid, set()).add(stype)

    return {
        "exams": [
            {
                "exam_id": e.exam_id,
                "title": e.title,
                "is_active": bool(e.is_active),
                "skills": sorted(s for s in skill_map.get(e.exam_id, set()) if s in ('reading', 'listening')),
            }
            for e in exams
        ]
    }


@router.get("/question-typing/exam/{exam_id}")
async def get_exam_question_types(
    exam_id: int,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Return the exam's parts and each question's current type for editing."""
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")

    sections = (
        db.query(ExamSection)
        .filter(
            ExamSection.exam_id == exam_id,
            ExamSection.section_type.in_(['reading', 'listening']),
        )
        .order_by(ExamSection.order_number)
        .all()
    )

    # Listening questions usually have no question_number, which broke the bulk
    # "assign by question range" (range match on number). Assign a sequential fallback
    # number across the exam (1..N, excluding main_text) so ranges work for both
    # reading (keeps its real numbers) and listening.
    seq = 0
    result_sections = []
    for section in sections:
        questions = (
            db.query(Question)
            .filter(
                Question.section_id == section.section_id,
                Question.question_type != 'main_text',
            )
            .order_by(Question.question_number, Question.question_id)
            .all()
        )
        q_list = []
        for q in questions:
            seq += 1
            num = q.question_number if (q.question_number and q.question_number > 0) else seq
            q_list.append({
                "question_id": q.question_id,
                "question_number": num,
                "question_type": q.stats_category or "",  # the assigned IELTS category (NOT the render type)
                "preview": _strip_html(q.question_text)[:120],
            })
        result_sections.append({
            "section_id": section.section_id,
            "order_number": section.order_number,
            "part_title": section.part_title,
            "section_type": section.section_type,
            "questions": q_list,
        })

    return {
        "exam_id": exam.exam_id,
        "title": exam.title,
        "sections": result_sections,
    }


class TypeAssignment(BaseModel):
    question_id: int
    question_type: str


class BulkAssignPayload(BaseModel):
    assignments: List[TypeAssignment]


@router.put("/question-typing/exam/{exam_id}")
async def bulk_assign_question_types(
    exam_id: int,
    payload: BulkAssignPayload,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Bulk-update question_type for the given questions of an exam."""
    if not payload.assignments:
        return {"success": True, "updated": 0}

    # Restrict updates to questions that actually belong to this exam.
    valid_ids = {
        row[0]
        for row in db.query(Question.question_id)
        .join(ExamSection, Question.section_id == ExamSection.section_id)
        .filter(ExamSection.exam_id == exam_id)
        .all()
    }

    updated = 0
    for a in payload.assignments:
        if a.question_id not in valid_ids:
            continue
        qtype = (a.question_type or "").strip()
        if not qtype:
            continue
        q = db.query(Question).filter(Question.question_id == a.question_id).first()
        if q:
            # Write the STATS category only — never touch question_type (render type),
            # otherwise the exam-taking UI breaks (fill_blank/multiple_choice/…).
            q.stats_category = qtype
            updated += 1

    db.commit()
    return {"success": True, "updated": updated}


# --------------------------------------------------------------------------
# Writing — assign a task type (question_type_tags) per Task 1 / Task 2 part.
# Writing has no per-question rows: a "part" is a WritingTask carrying a
# question_type_tags JSON list. The student writing overview (Results Overview)
# groups band scores by these tags. Values match ManageForecast.jsx so the two
# admin tools stay consistent.
# --------------------------------------------------------------------------

@router.get("/question-typing/writing-exams")
async def list_writing_exams(
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Exams that have writing tasks, for the exam picker (writing mode)."""
    exam_ids = [row[0] for row in db.query(distinct(WritingTask.test_id)).all() if row[0]]
    if not exam_ids:
        return {"exams": []}
    exams = (
        db.query(Exam)
        .filter(Exam.exam_id.in_(exam_ids))
        .order_by(Exam.exam_id.desc())
        .all()
    )
    return {
        "exams": [
            {"exam_id": e.exam_id, "title": e.title, "is_active": bool(e.is_active), "skills": ["writing"]}
            for e in exams
        ]
    }


@router.get("/question-typing/writing/{exam_id}")
async def get_writing_task_types(
    exam_id: int,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Return an exam's writing tasks (Task 1 / Task 2) with their current type."""
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
    tasks = (
        db.query(WritingTask)
        .filter(WritingTask.test_id == exam_id)
        .order_by(WritingTask.part_number, WritingTask.task_id)
        .all()
    )
    return {
        "exam_id": exam.exam_id,
        "title": exam.title,
        "tasks": [
            {
                "task_id": t.task_id,
                "part_number": t.part_number,
                "title": t.title,
                # Single primary category for this tool (first tag); a task normally
                # carries one writing type. ManageForecast can still set multiple.
                "question_type": (t.question_type_tags or [""])[0] if t.question_type_tags else "",
            }
            for t in tasks
        ],
    }


class WritingTypeAssignment(BaseModel):
    task_id: int
    question_type: str


class WritingBulkPayload(BaseModel):
    assignments: List[WritingTypeAssignment]


@router.put("/question-typing/writing/{exam_id}")
async def bulk_assign_writing_types(
    exam_id: int,
    payload: WritingBulkPayload,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin),
):
    """Set question_type_tags for the given writing tasks of an exam."""
    if not payload.assignments:
        return {"success": True, "updated": 0}
    valid_ids = {
        row[0] for row in db.query(WritingTask.task_id).filter(WritingTask.test_id == exam_id).all()
    }
    updated = 0
    for a in payload.assignments:
        if a.task_id not in valid_ids:
            continue
        t = db.query(WritingTask).filter(WritingTask.task_id == a.task_id).first()
        if not t:
            continue
        qtype = (a.question_type or "").strip()
        t.question_type_tags = [qtype] if qtype else []
        updated += 1
    db.commit()
    return {"success": True, "updated": updated}
