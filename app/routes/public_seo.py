from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Exam, ExamSection, WritingTask, SpeakingMaterial
from typing import List

router = APIRouter()

@router.get("/listening-tests", response_model=List[dict])
async def get_public_listening_tests(db: Session = Depends(get_db)):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'listening'
    ).distinct().all()

    exam_ids = [e.exam_id for e in exams]
    if not exam_ids:
        return []

    all_sections = db.query(ExamSection).filter(
        ExamSection.exam_id.in_(exam_ids),
        ExamSection.section_type == 'listening'
    ).order_by(ExamSection.order_number).all()
    
    sections_by_exam = {}
    for s in all_sections:
        sections_by_exam.setdefault(s.exam_id, []).append(s)

    result = []
    for exam in exams:
        sections = sections_by_exam.get(exam.exam_id, [])
        if not sections:
            continue
        first_section = sections[0]
        part_titles = {s.order_number: s.part_title for s in sections if s.part_title}
        
        result.append({
            "exam_id": exam.exam_id,
            "title": exam.title,
            "created_at": exam.created_at,
            "duration": first_section.duration,
            "total_marks": first_section.total_marks,
            "is_completed": False,
            "total_score": 0,
            "part_titles": part_titles
        })
    return result

@router.get("/reading-tests", response_model=List[dict])
async def get_public_reading_tests(db: Session = Depends(get_db)):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'reading'
    ).distinct().all()

    exam_ids = [e.exam_id for e in exams]
    if not exam_ids:
        return []

    all_sections = db.query(ExamSection).filter(
        ExamSection.exam_id.in_(exam_ids),
        ExamSection.section_type == 'reading'
    ).order_by(ExamSection.order_number).all()
    
    sections_by_exam = {}
    for s in all_sections:
        sections_by_exam.setdefault(s.exam_id, []).append(s)

    result = []
    for exam in exams:
        sections = sections_by_exam.get(exam.exam_id, [])
        if not sections:
            continue
        first_section = sections[0]
        part_titles = {s.order_number: s.part_title for s in sections if s.part_title}
        
        result.append({
            "exam_id": exam.exam_id,
            "title": exam.title,
            "created_at": exam.created_at,
            "duration": first_section.duration,
            "total_marks": first_section.total_marks,
            "is_completed": False,
            "total_score": 0,
            "part_titles": part_titles
        })
    return result

@router.get("/writing-forecasts", response_model=List[dict])
async def get_public_writing_forecasts(db: Session = Depends(get_db)):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'essay'
    ).distinct().all()

    exam_ids = [e.exam_id for e in exams]
    if not exam_ids:
        return []

    all_forecast_tasks = db.query(WritingTask).filter(
        WritingTask.test_id.in_(exam_ids),
        WritingTask.is_forecast == True
    ).order_by(WritingTask.part_number).all()
    
    tasks_by_exam = {}
    for t in all_forecast_tasks:
        tasks_by_exam.setdefault(t.test_id, []).append(t)

    result = []
    for exam in exams:
        forecast_tasks = tasks_by_exam.get(exam.exam_id, [])
        if not forecast_tasks:
            continue
        result.append({
            "exam_id": exam.exam_id,
            "exam_title": exam.title,
            "parts": [{
                "task_id": t.task_id,
                "part_number": t.part_number,
                "title": t.title,
                "task_type": t.task_type,
                "instructions": "",
                "word_limit": t.word_limit,
                "is_recommended": bool(getattr(t, 'is_recommended', False))
            } for t in forecast_tasks]
        })
    return result

@router.get("/listening-forecasts", response_model=List[dict])
async def get_public_listening_forecasts(db: Session = Depends(get_db)):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'listening'
    ).distinct().all()

    result = []
    for exam in exams:
        sections = db.query(ExamSection).filter(
            ExamSection.exam_id == exam.exam_id,
            ExamSection.section_type == 'listening'
        ).order_by(ExamSection.order_number).all()

        forecast_sections = [s for s in sections if getattr(s, 'is_forecast', False)]
        if not forecast_sections:
            continue

        forecast_parts = []
        for s in forecast_sections:
            forecast_parts.append({
                "part_number": s.order_number,
                "forecast_title": getattr(s, 'forecast_title', None),
                "completed": False,
                "attempts_count": 0,
                "is_recommended": bool(getattr(s, 'is_recommended', False)),
                "question_types": getattr(s, 'question_types', None) or []
            })

        result.append({
            "exam_id": exam.exam_id,
            "exam_title": exam.title,
            "parts": forecast_parts
        })
    return result

@router.get("/reading-forecasts", response_model=List[dict])
async def get_public_reading_forecasts(db: Session = Depends(get_db)):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'reading'
    ).distinct().all()

    result = []
    for exam in exams:
        sections = db.query(ExamSection).filter(
            ExamSection.exam_id == exam.exam_id,
            ExamSection.section_type == 'reading'
        ).order_by(ExamSection.order_number).all()

        forecast_sections = [s for s in sections if getattr(s, 'is_forecast', False)]
        if not forecast_sections:
            continue

        forecast_parts = []
        for s in forecast_sections:
            forecast_parts.append({
                "part_number": s.order_number,
                "forecast_title": getattr(s, 'forecast_title', None),
                "completed": False,
                "attempts_count": 0,
                "is_recommended": bool(getattr(s, 'is_recommended', False)),
                "question_types": getattr(s, 'question_types', None) or []
            })

        result.append({
            "exam_id": exam.exam_id,
            "exam_title": exam.title,
            "parts": forecast_parts
        })
    return result

@router.get("/speaking/materials", response_model=List[dict])
async def get_public_speaking_materials(part: str = None, db: Session = Depends(get_db)):
    query = db.query(SpeakingMaterial)
    if part:
        query = query.filter(SpeakingMaterial.part_type == part)
    materials = query.order_by(SpeakingMaterial.created_at.desc()).all()
    
    results = []
    for m in materials:
        results.append({
            "material_id": m.material_id,
            "title": m.title,
            "part_type": m.part_type,
            "pdf_url": m.pdf_url,
            "created_at": m.created_at,
            "has_access": False
        })
    return results
