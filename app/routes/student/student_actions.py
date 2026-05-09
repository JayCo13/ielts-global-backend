from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Request
from sqlalchemy.orm import Session, joinedload  # Add joinedload here
from typing import Optional
from app.database import get_db
from app.models.models import ExamAccessType, User, ExamResult, Exam, ExamSection, Question, QuestionOption, ReadingPassage, ListeningMedia, WritingTask, StudentAnswer, WritingAnswer, ListeningAnswer, SpeakingMaterial
from app.routes.admin.auth import get_current_student, check_exam_access
from typing import List, Dict
from bs4 import BeautifulSoup
from fastapi.responses import StreamingResponse
from mutagen.mp3 import MP3
import subprocess
import tempfile
import io
import os
import re 
from sqlalchemy import and_, or_
from uuid import uuid4
from pydantic import BaseModel
from app.utils.datetime_utils import get_vietnam_time, convert_to_vietnam_time
from datetime import datetime, timedelta
from app.utils.redis_cache import cache, get_listening_test_cache_key, get_audio_metadata_cache_key
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


UPLOAD_DIR = "static/student_images"

@router.get("/speaking/materials", response_model=List[dict])
async def student_list_speaking_materials(
    part: Optional[str] = None,
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    query = db.query(SpeakingMaterial).options(joinedload(SpeakingMaterial.access_types))
    if part:
        query = query.filter(SpeakingMaterial.part_type == part)
    materials = query.order_by(SpeakingMaterial.created_at.desc()).all()
    
    # Determine allowed access types based on user role
    allowed_types = []
    if current_student.role == 'student':
        allowed_types = ['student']
    elif current_student.role == 'customer':
        if current_student.is_vip:
            allowed_types = ['no vip', 'vip']
        else:
            allowed_types = ['no vip']
            
    results = []
    for m in materials:
        material_types = [a.access_type for a in m.access_types]
        # Fallback: if no access types defined, allow access (legacy support)
        if not material_types:
            has_access = True
        else:
            has_access = any(t in allowed_types for t in material_types)
        
        results.append({
            "material_id": m.material_id,
            "title": m.title,
            "part_type": m.part_type,
            "pdf_url": m.pdf_url,
            "created_at": m.created_at,
            "has_access": has_access
        })
    return results

@router.get("/speaking/materials/{material_id}", response_model=dict)
async def student_get_speaking_material(
    material_id: int,
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    m = db.query(SpeakingMaterial).filter(SpeakingMaterial.material_id == material_id)\
        .options(joinedload(SpeakingMaterial.access_types)).first()
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    
    # Access Check
    allowed_types = []
    if current_student.role == 'student':
        allowed_types = ['student']
    elif current_student.role == 'customer':
        if current_student.is_vip:
            allowed_types = ['no vip', 'vip']
        else:
            allowed_types = ['no vip']
            
    material_types = [a.access_type for a in m.access_types]
    if not material_types:
        has_access = True
    else:
        has_access = any(t in allowed_types for t in material_types)
    
    if not has_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    return {
        "material_id": m.material_id,
        "title": m.title,
        "part_type": m.part_type,
        "pdf_url": m.pdf_url,
        "created_at": m.created_at,
        "has_access": True
    }


class UpdateProfileRequest(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None

class ExamResultResponse(BaseModel):
    exam_id: int
    exam_title: str
    total_score: float
    completion_date: datetime
    section_scores: dict

class WritingAnswerSubmit(BaseModel):
    answer_text: str
class WritingTestSubmit(BaseModel):
    part1_answer: str
    part2_answer: str

@router.get("/user-role/{user_id}", response_model=dict)
async def get_user_role_by_id(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student)
):
    """Retrieve a user's role by their ID"""
    # Query the user by ID
    user = db.query(User).filter(User.user_id == user_id).first()
    
    # If user not found, raise 404 error
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found"
        )
    
    # Return the user's role
    return {
        "user_id": user.user_id,
        "role": user.role
    }

@router.get("/profile", response_model=dict)
async def get_student_profile(
    request: Request,
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    image_url = f"{request.base_url}{current_student.image_url}" if current_student.image_url else None
    
    return {
        "user_id": current_student.user_id,
        "username": current_student.username,
        "email": current_student.email,
        "image_url": image_url,
        "role": current_student.role,  # Include role for frontend verification
        "is_google_account": bool(current_student.google_id)  # True if registered via Google
    }
@router.get("/exam/{exam_id}/audio")
async def stream_combined_audio(
    exam_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    # Get all audio files for the specified exam
    listening_media_files = (
        db.query(ListeningMedia)
        .join(ExamSection)
        .filter(ExamSection.exam_id == exam_id)
        .order_by(ExamSection.order_number)
        .all()
    )
    
    if not listening_media_files:
        raise HTTPException(
            status_code=404, 
            detail="No audio files found for this exam"
        )
    
    import requests as http_requests

    # Download audio files (from R2 URL or LONGBLOB)
    temp_files = []
    total_duration = 0
    for media in listening_media_files:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        
        if media.audio_url:
            # Download from R2
            r2_response = http_requests.get(media.audio_url)
            temp_file.write(r2_response.content)
        elif media.audio_file:
            # Legacy: from LONGBLOB
            temp_file.write(media.audio_file)
        else:
            temp_file.close()
            continue
            
        temp_file.close()
        temp_files.append(temp_file.name)
        
        # Calculate duration
        try:
            audio_data_file = open(temp_file.name, "rb")
            audio = MP3(audio_data_file)
            total_duration += audio.info.length
            audio_data_file.close()
        except Exception:
            pass
    
    if not temp_files:
        raise HTTPException(status_code=404, detail="No playable audio files found")
    
    # Create a temporary file for the combined audio
    combined_audio_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    combined_audio_file.close()
    
    # Use ffmpeg to combine audio files
    ffmpeg_command = [
        "ffmpeg", "-y", "-i", f"concat:{'|'.join(temp_files)}",
        "-c", "copy", combined_audio_file.name
    ]
    subprocess.run(ffmpeg_command, check=True)
    
    # Get file size for Content-Length header
    file_size = os.path.getsize(combined_audio_file.name)
    
    # Handle Range header for seeking
    start = 0
    end = file_size - 1
    status_code = 200
    
    # Check if Range header exists
    range_header = request.headers.get("Range", None)
    if range_header:
        range_match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if range_match:
            start = int(range_match.group(1))
            end_group = range_match.group(2)
            if end_group:
                end = int(end_group)
            status_code = 206  # Partial Content
    
    # Calculate content length based on range
    content_length = end - start + 1
    
    # Create a file-like object for the response
    def iterfile():
        with open(combined_audio_file.name, "rb") as f:
            f.seek(start)
            data = f.read(min(content_length, 1024 * 1024))  # Read in 1MB chunks
            while data:
                yield data
                if len(data) < 1024 * 1024:
                    break
                data = f.read(min(content_length - f.tell() + start, 1024 * 1024))
        
        # Clean up temporary files after streaming is complete
        for temp_file in temp_files:
            try:
                os.unlink(temp_file)
            except:
                pass
        try:
            os.unlink(combined_audio_file.name)
        except:
            pass
    
    # Set appropriate headers for streaming and seeking
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}" if status_code == 206 else None,
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": "audio/mpeg",
        "Content-Disposition": f"inline; filename=exam_{exam_id}_combined.mp3",
        "X-Total-Duration": str(int(total_duration))
    }
    
    # Remove None values from headers
    headers = {k: v for k, v in headers.items() if v is not None}
    
    return StreamingResponse(
        iterfile(),
        status_code=status_code,
        headers=headers
    )
@router.get("/my-test-statistics", response_model=Dict)
async def get_student_test_statistics(
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    from sqlalchemy.sql import func
    
    # 1) Get all exam results with exam titles in ONE query (joinedload)
    exam_results = db.query(ExamResult).options(
        joinedload(ExamResult.exam)
    ).filter(
        ExamResult.user_id == current_student.user_id
    ).order_by(ExamResult.completion_date.desc()).all()
    
    # Get latest test if available
    latest_test = None
    if exam_results:
        latest_result = exam_results[0]
        latest_test = {
            "result_id": latest_result.result_id,
            "exam_id": latest_result.exam_id,
            "exam_title": latest_result.exam.title,
            "total_score": latest_result.total_score,
            "completion_date": latest_result.completion_date,
            "section_scores": latest_result.section_scores
        }
    
    # 2) Batch load: which exam_ids are listening exams? (ONE query)
    all_exam_ids = list(set(r.exam_id for r in exam_results))
    listening_exam_ids = set()
    if all_exam_ids:
        listening_sections = db.query(ExamSection.exam_id).filter(
            ExamSection.exam_id.in_(all_exam_ids),
            ExamSection.section_type == 'listening'
        ).distinct().all()
        listening_exam_ids = set(row[0] for row in listening_sections)
    
    # 3) Batch load: listening answer stats per (exam_id, result_id) (ONE query)
    listening_result_ids = [r.result_id for r in exam_results if r.exam_id in listening_exam_ids]
    answer_stats = {}  # result_id -> (correct_count, total_count)
    if listening_result_ids:
        stats_rows = db.query(
            ListeningAnswer.result_id,
            func.sum(func.IF(ListeningAnswer.score > 0, 1, 0)).label('correct'),
            func.count(ListeningAnswer.answer_id).label('total')
        ).filter(
            ListeningAnswer.result_id.in_(listening_result_ids)
        ).group_by(ListeningAnswer.result_id).all()
        for rid, correct, total in stats_rows:
            answer_stats[rid] = (int(correct), int(total))
    
    # Build listening statistics from pre-loaded data
    listening_exams = []
    listening_total_questions = 0
    listening_correct_answers = 0
    
    for result in exam_results:
        if result.exam_id not in listening_exam_ids:
            continue
        correct_count, total_questions = answer_stats.get(result.result_id, (0, 0))
        exam_accuracy = (correct_count / total_questions * 100) if total_questions > 0 else 0
        
        listening_exams.append({
            "result_id": result.result_id,
            "exam_id": result.exam_id,
            "exam_title": result.exam.title,
            "total_score": result.total_score,
            "accuracy": exam_accuracy,
            "completion_date": result.completion_date
        })
        listening_total_questions += total_questions
        listening_correct_answers += correct_count
    
    # Calculate overall listening statistics
    listening_accuracy = 0
    listening_avg_score = 0
    if listening_total_questions > 0:
        listening_accuracy = (listening_correct_answers / listening_total_questions) * 100
        listening_avg_score = sum(e["total_score"] for e in listening_exams) / len(listening_exams)
    
    # 4) Writing stats: batch load all tasks + answers (TWO queries instead of 3N)
    all_writing_tasks = db.query(WritingTask).all()
    all_task_ids = [t.task_id for t in all_writing_tasks]
    
    user_writing_answers = []
    if all_task_ids:
        user_writing_answers = db.query(WritingAnswer).filter(
            WritingAnswer.task_id.in_(all_task_ids),
            WritingAnswer.user_id == current_student.user_id
        ).all()
    
    # Group tasks and answers by test_id
    tasks_by_test = {}
    for t in all_writing_tasks:
        tasks_by_test.setdefault(t.test_id, []).append(t)
    
    answers_by_task = {a.task_id: a for a in user_writing_answers}
    
    # Build exam title lookup from already-loaded data + batch query for writing exams
    writing_test_ids = [tid for tid in tasks_by_test.keys() 
                        if any(t.task_id in answers_by_task for t in tasks_by_test[tid])]
    exam_titles = {}
    if writing_test_ids:
        title_rows = db.query(Exam.exam_id, Exam.title).filter(
            Exam.exam_id.in_(writing_test_ids)
        ).all()
        exam_titles = {eid: title for eid, title in title_rows}
    
    writing_tests = []
    writing_tasks_completed = 0
    
    for test_id, tasks in tasks_by_test.items():
        task_ids = [t.task_id for t in tasks]
        test_answers = [answers_by_task[tid] for tid in task_ids if tid in answers_by_task]
        
        if test_answers:
            writing_tests.append({
                "test_id": test_id,
                "title": exam_titles.get(test_id, ""),
                "parts_completed": len(test_answers),
                "total_parts": len(tasks),
                "is_completed": len(test_answers) == len(tasks),
                "latest_update": max(a.updated_at for a in test_answers)
            })
            writing_tasks_completed += len(test_answers)
    
    return {
        "total_exams_completed": len(exam_results),
        "latest_test": latest_test,
        "listening_statistics": {
            "exams_completed": len(listening_exams),
            "average_accuracy": round(listening_accuracy, 2),
            "average_score": round(listening_avg_score, 2) if listening_exams else 0,
            "exams": listening_exams
        },
        "writing_statistics": {
            "tests_attempted": len(writing_tests),
            "tasks_completed": writing_tasks_completed,
            "tests": writing_tests
        }
    }

@router.put("/profile", response_model=dict)
async def update_student_profile(
    email: str = Form(None),
    username: str = Form(None),
    image: UploadFile = File(None),
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    if email:
        current_student.email = email
    
    if username:
        current_student.username = username
    
    if image:
        file_extension = os.path.splitext(image.filename)[1]
        unique_filename = f"{uuid4()}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        
        with open(file_path, "wb") as buffer:
            content = await image.read()
            buffer.write(content)
        
        current_student.image_url = file_path

    db.commit()
    db.refresh(current_student)
    
    return {
        "message": "Profile updated successfully",
        "email": current_student.email,
        "username": current_student.username,
        "image_url": current_student.image_url
    }
    
@router.get("/available-listening-exams", response_model=List[dict])
async def get_available_listening_exams(
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # 1) Get all active exams with listening sections (ONE query)
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'listening'
    ).distinct().order_by(Exam.exam_id).all()

    if not exams:
        return []

    exam_ids = [e.exam_id for e in exams]
    
    # 2) Batch load all access types for these exams (ONE query)
    all_access_types = db.query(ExamAccessType).filter(
        ExamAccessType.exam_id.in_(exam_ids)
    ).all()
    access_by_exam = {}
    for a in all_access_types:
        access_by_exam.setdefault(a.exam_id, []).append(a.access_type)
    
    # 3) Batch load all listening sections (ONE query)
    all_sections = db.query(ExamSection).filter(
        ExamSection.exam_id.in_(exam_ids),
        ExamSection.section_type == 'listening'
    ).order_by(ExamSection.order_number).all()
    sections_by_exam = {}
    for s in all_sections:
        sections_by_exam.setdefault(s.exam_id, []).append(s)
    
    # 4) Batch load latest non-forecast results (ONE query with window function alternative)
    from sqlalchemy.sql import func
    latest_results_sub = db.query(
        ExamResult.exam_id,
        func.max(ExamResult.completion_date).label('max_date')
    ).filter(
        ExamResult.exam_id.in_(exam_ids),
        ExamResult.user_id == current_student.user_id,
        ExamResult.is_forecast.in_([False, None])
    ).group_by(ExamResult.exam_id).subquery()
    
    latest_results = db.query(ExamResult).join(
        latest_results_sub,
        and_(
            ExamResult.exam_id == latest_results_sub.c.exam_id,
            ExamResult.completion_date == latest_results_sub.c.max_date
        )
    ).filter(
        ExamResult.user_id == current_student.user_id
    ).all()
    results_by_exam = {r.exam_id: r for r in latest_results}

    # Determine allowed access types
    allowed_types = []
    if current_student.role == 'student':
        allowed_types = ['student']
    elif current_student.role == 'customer':
        if current_student.is_vip:
            allowed_types = ['no vip', 'vip']
        else:
            allowed_types = ['no vip']

    # Build response using pre-loaded data
    exam_details = []
    for exam in exams:
        exam_types = access_by_exam.get(exam.exam_id, [])
        if not any(at in allowed_types for at in exam_types):
            continue
        
        sections = sections_by_exam.get(exam.exam_id, [])
        if not sections:
            continue
        
        first_section = sections[0]
        part_titles = {}
        for s in sections:
            if s.part_title:
                part_titles[s.order_number] = s.part_title
        
        latest = results_by_exam.get(exam.exam_id)
        
        exam_details.append({
            "exam_id": exam.exam_id,
            "title": exam.title,
            "created_at": exam.created_at,
            "duration": first_section.duration,
            "total_marks": first_section.total_marks,
            "is_completed": latest is not None,
            "total_score": latest.total_score if latest else 0,
            "part_titles": part_titles
        })

    return exam_details


@router.get("/writing/forecasts", response_model=List[dict])
async def get_writing_forecasts(
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # 1) Get all active exams with essay sections
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'essay'
    ).distinct().order_by(Exam.exam_id).all()

    if not exams:
        return []

    exam_ids = [e.exam_id for e in exams]

    # 2) Batch load access types (ONE query)
    all_access = db.query(ExamAccessType).filter(
        ExamAccessType.exam_id.in_(exam_ids)
    ).all()
    access_by_exam = {}
    for a in all_access:
        access_by_exam.setdefault(a.exam_id, []).append(a.access_type)

    # 3) Batch load all forecast writing tasks (ONE query)
    all_forecast_tasks = db.query(WritingTask).filter(
        WritingTask.test_id.in_(exam_ids),
        WritingTask.is_forecast == True
    ).order_by(WritingTask.part_number).all()
    tasks_by_exam = {}
    for t in all_forecast_tasks:
        tasks_by_exam.setdefault(t.test_id, []).append(t)

    # Determine allowed access types
    allowed_types = []
    if current_student.role == 'student':
        allowed_types = ['student']
    elif current_student.role == 'customer':
        allowed_types = ['no vip', 'vip'] if current_student.is_vip else ['no vip']

    # Build response using pre-loaded data
    result = []
    for exam in exams:
        exam_types = access_by_exam.get(exam.exam_id, [])
        if not any(at in allowed_types for at in exam_types):
            continue

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
                "instructions": t.instructions,
                "word_limit": t.word_limit,
                "is_recommended": bool(getattr(t, 'is_recommended', False))

            } for t in forecast_tasks]
        })
    return result

@router.get("/listening/forecasts", response_model=List[dict])
async def get_listening_forecasts(
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'listening'
    ).distinct().order_by(Exam.exam_id).all()

    result = []
    from sqlalchemy.sql import func
    for exam in exams:
        exam_access_types = db.query(ExamAccessType).filter(ExamAccessType.exam_id == exam.exam_id).all()
        allowed_types = []
        if current_student.role == 'student':
            allowed_types = ['student']
        elif current_student.role == 'customer':
            allowed_types = ['no vip', 'vip'] if getattr(current_student, 'is_vip', False) else ['no vip']
        exam_types = [access.access_type for access in exam_access_types]
        if not any(access in allowed_types for access in exam_types):
            continue

        sections = db.query(ExamSection).filter(
            ExamSection.exam_id == exam.exam_id,
            ExamSection.section_type == 'listening'
        ).order_by(ExamSection.order_number).all()

        forecast_sections = [s for s in sections if getattr(s, 'is_forecast', False)]
        if not forecast_sections:
            continue

        section_ids = [s.section_id for s in forecast_sections]
        expected_map_rows = db.query(
            Question.section_id,
            func.count(Question.question_id).label('expected_cnt')
        ).filter(
            Question.section_id.in_(section_ids),
            Question.question_type != 'main_text'
        ).group_by(Question.section_id).all()
        expected_map = {sid: cnt for sid, cnt in expected_map_rows}

        res_ids = [r.result_id for r in db.query(ExamResult.result_id).filter(
            ExamResult.user_id == current_student.user_id,
            ExamResult.exam_id == exam.exam_id
        ).all()]

        attempts_by_section = {}
        if res_ids:
            rows_listen = db.query(
                ListeningAnswer.result_id,
                Question.section_id,
                func.count(ListeningAnswer.answer_id).label('cnt')
            ).join(Question, ListeningAnswer.question_id == Question.question_id)\
             .filter(
                ListeningAnswer.result_id.in_(res_ids),
                Question.section_id.in_(section_ids)
             )\
             .group_by(ListeningAnswer.result_id, Question.section_id)\
             .all()

            rows = rows_listen
            if not rows_listen:
                rows_student = db.query(
                    StudentAnswer.result_id,
                    Question.section_id,
                    func.count(StudentAnswer.answer_id).label('cnt')
                ).join(Question, StudentAnswer.question_id == Question.question_id)\
                 .filter(
                    StudentAnswer.result_id.in_(res_ids),
                    Question.section_id.in_(section_ids)
                 )\
                 .group_by(StudentAnswer.result_id, Question.section_id)\
                 .all()
                rows = rows_student

            for rid, sid, cnt in rows:
                attempts_by_section.setdefault(sid, []).append((rid, cnt))

        forecast_parts = []
        for s in forecast_sections:
            expected = expected_map.get(s.section_id, 0)
            candidates = attempts_by_section.get(s.section_id, [])
            attempts_count = sum(1 for _, cnt in candidates if cnt > 0)
            forecast_parts.append({
                "part_number": s.order_number,
                "forecast_title": getattr(s, 'forecast_title', None),
                "completed": attempts_count > 0,
                "attempts_count": attempts_count,
                "is_recommended": bool(getattr(s, 'is_recommended', False)),
                "question_types": getattr(s, 'question_types', None) or []
            })

        result.append({
            "exam_id": exam.exam_id,
            "exam_title": exam.title,
            "parts": forecast_parts
        })
    return result

@router.get("/listening/forecast-history/{exam_id}/{part_number}", response_model=List[dict])
async def get_listening_forecast_history(
    exam_id: int,
    part_number: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # Get the section for this forecast part
    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.section_type == 'listening',
        ExamSection.order_number == part_number
    ).first()
    if not section:
        return []
    
    from sqlalchemy.sql import func
    
    # Calculate total marks for this section (excluding main_text questions)
    total_marks = db.query(func.sum(Question.marks)).filter(
        Question.section_id == section.section_id,
        Question.question_type != 'main_text'
    ).scalar() or 0

    # Use database columns to filter forecast results directly
    forecast_results = db.query(ExamResult).filter(
        ExamResult.user_id == current_student.user_id,
        ExamResult.exam_id == exam_id,
        ExamResult.is_forecast == True,
        ExamResult.forecast_part == part_number
    ).order_by(ExamResult.completion_date.desc()).all()

    # Calculate earned scores for each forecast result
    attempts = []
    for result in forecast_results:
        # Get earned score for this forecast result from ListeningAnswer
        earned_score = db.query(func.sum(ListeningAnswer.score)).join(
            Question, ListeningAnswer.question_id == Question.question_id
        ).filter(
            ListeningAnswer.result_id == result.result_id,
            Question.section_id == section.section_id
        ).scalar() or 0
        
        # If no ListeningAnswer, try StudentAnswer
        if earned_score == 0:
            earned_score = db.query(func.sum(StudentAnswer.score)).join(
                Question, StudentAnswer.question_id == Question.question_id
            ).filter(
                StudentAnswer.result_id == result.result_id,
                Question.section_id == section.section_id
            ).scalar() or 0
        
        attempts.append({
            'result_id': result.result_id,
            'completion_date': result.completion_date,
            'attempt_number': result.attempt_number,
            'score_earned': float(earned_score),
            'score_total': int(total_marks)
        })
    
    return attempts

@router.get("/writing/forecast/{task_id}", response_model=dict)
async def get_writing_forecast_detail(
    task_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    task = db.query(WritingTask).filter(WritingTask.task_id == task_id, WritingTask.is_forecast == True).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Forecast not found")
    exam = db.query(Exam).filter(Exam.exam_id == task.test_id).first()

    exam_access_types = db.query(ExamAccessType).filter(ExamAccessType.exam_id == task.test_id).all()
    allowed_types = []
    if current_student.role == 'student':
        allowed_types = ['student']
    elif current_student.role == 'customer':
        allowed_types = ['no vip', 'vip'] if current_student.is_vip else ['no vip']
    exam_types = [access.access_type for access in exam_access_types]
    if not any(access in allowed_types for access in exam_types):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    return {
        "exam_id": task.test_id,
        "exam_title": exam.title if exam else None,
        "task_id": task.task_id,
        "part_number": task.part_number,
        "title": task.title,
        "task_type": task.task_type,
        "instructions": task.instructions,
        "sample_essay": getattr(task, 'sample_essay', None),
        "word_limit": task.word_limit
    }

@router.delete("/listening/exam/{exam_id}/retake", response_model=dict)
async def retake_exam(
    exam_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # Verify exam exists
    exam = db.query(Exam).filter(
        Exam.exam_id == exam_id,
        Exam.is_active == True
    ).first()
    
    if not exam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exam not found or not active"
        )
    
    # Count existing attempts for this exam
    existing_attempts = db.query(ExamResult).filter(
        ExamResult.exam_id == exam_id,
        ExamResult.user_id == current_student.user_id
    ).count()
    
    # No need to delete anything - just allow a new attempt
    # Previous results are preserved for exam history
    
    return {
        "message": f"Ready for exam attempt #{existing_attempts + 1}. Previous attempts are preserved in your exam history.",
        "exam_id": exam_id,
        "attempt_number": existing_attempts + 1,
        "previous_attempts": existing_attempts
    }

@router.get("/exam/{exam_id}/audio-part/{part_number}", response_model=Dict)
async def get_exam_audio_part(
    exam_id: int,
    part_number: int,
    request: Request,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Get a specific audio part for an exam"""
    
    if not 1 <= part_number <= 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Part number must be between 1 and 4"
        )
    
    # Verify exam exists and is active
    exam = db.query(Exam).filter(
        Exam.exam_id == exam_id,
        Exam.is_active == True
    ).first()
    
    if not exam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exam not found or not active"
        )
    
    # Check if user has access to this exam
    has_access = check_exam_access(db, exam_id, current_student)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this exam"
        )
    
    # Get the specific audio file for this exam part
    listening_media = db.query(ListeningMedia)\
        .join(ExamSection)\
        .filter(
            ExamSection.exam_id == exam_id,
            ExamSection.order_number == part_number
        ).first()
    
    if not listening_media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audio file found for exam {exam_id} part {part_number}"
        )
    
    # If audio is stored in R2, return the URL for direct access
    if listening_media.audio_url:
        return {"audio_url": listening_media.audio_url, "type": "r2"}
    
    if not listening_media.audio_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audio file found for exam {exam_id} part {part_number}"
        )
    
    # Create a BytesIO object from the audio data
    audio_data = io.BytesIO(listening_media.audio_file)
    
    # Get file size for Content-Length header
    audio_data.seek(0, io.SEEK_END)
    file_size = audio_data.tell()
    audio_data.seek(0)
    
    # Handle Range header for seeking
    start = 0
    end = file_size - 1
    status_code = 200
    
    # Check if Range header exists
    range_header = request.headers.get("Range", None)
    if range_header:
        range_match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if range_match:
            start = int(range_match.group(1))
            end_group = range_match.group(2)
            if end_group:
                end = int(end_group)
            status_code = 206  # Partial Content
    
    # Calculate content length based on range
    content_length = end - start + 1
    
    # Create a file-like object for the response
    def iterfile():
        audio_data.seek(start)
        data = audio_data.read(min(content_length, 1024 * 1024))  # Read in 1MB chunks
        while data:
            yield data
            if len(data) < 1024 * 1024:
                break
            data = audio_data.read(min(content_length - audio_data.tell() + start, 1024 * 1024))
    
    # Set appropriate headers for streaming and seeking
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}" if status_code == 206 else None,
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": "audio/mpeg",
        "Content-Disposition": f"inline; filename=exam_{exam_id}_part_{part_number}.mp3",
    }
    
    # Remove None values from headers
    headers = {k: v for k, v in headers.items() if v is not None}
    
    return StreamingResponse(
        iterfile(),
        status_code=status_code,
        headers=headers
    )


@router.get("/exam/{exam_id}/audio-lengths", response_model=Dict)
async def get_audio_file_lengths(
    exam_id: int,
    db: Session = Depends(get_db)
):
    # Try to get from cache first
    cache_key = get_audio_metadata_cache_key(exam_id)
    cached_result = await cache.get(cache_key)
    
    if cached_result:
        logger.info(f"Audio lengths for exam {exam_id} served from cache")
        return cached_result
    
    # Get all audio files for this exam through listening media
    # Use .with_entities to avoid loading LONGBLOB audio_file column
    listening_media = db.query(ListeningMedia)\
        .join(ExamSection)\
        .filter(ExamSection.exam_id == exam_id)\
        .all()
    
    if not listening_media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No audio files found for this exam"
        )
    
    part_lengths = []
    total_length = 0
    needs_fallback = []  # media items without stored duration
    
    for i, media in enumerate(listening_media):
        # Use stored duration from DB if available (no download needed!)
        if media.duration and media.duration > 0:
            length = int(media.duration)
            total_length += length
            part_data = {
                "part_number": i + 1,
                "length": length,
                "length_formatted": f"{length // 60}:{length % 60:02d}",
            }
            if media.audio_url:
                part_data["audio_url"] = media.audio_url
            part_lengths.append(part_data)
        else:
            # Duration not stored — need fallback download (only for missing durations)
            needs_fallback.append((i, media))
            part_lengths.append({
                "part_number": i + 1,
                "length": 0,
                "length_formatted": "00:00",
            })
    
    # Fallback: download and parse only for media without stored duration
    if needs_fallback:
        import requests as http_requests
        for idx, media in needs_fallback:
            try:
                if media.audio_url:
                    r2_response = http_requests.get(media.audio_url, timeout=15)
                    audio_data = io.BytesIO(r2_response.content)
                elif media.audio_file:
                    audio_data = io.BytesIO(media.audio_file)
                else:
                    continue
                
                audio = MP3(audio_data)
                length = int(audio.info.length)
                total_length += length
                part_lengths[idx]["length"] = length
                part_lengths[idx]["length_formatted"] = f"{length // 60}:{length % 60:02d}"
                if media.audio_url:
                    part_lengths[idx]["audio_url"] = media.audio_url
                
                # Save duration to DB so we never need to download again
                media.duration = length
                db.add(media)
            except Exception as e:
                part_lengths[idx]["error"] = str(e)
        
        try:
            db.commit()
        except Exception:
            pass
    
    # Prepare result
    result = {
        "exam_id": exam_id,
        "total_length": total_length,
        "total_length_formatted": f"{total_length // 60}:{total_length % 60:02d}",
        "part_lengths": part_lengths,
        "parts_count": len(listening_media)
    }
    

    # Cache the result for 2 hours
    await cache.set(cache_key, result, ttl=7200)
    logger.info(f"Cached audio metadata for exam {exam_id}")
    
    return result
@router.put("/status/update", response_model=dict)
async def update_student_status(
    status: str,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """
    Update the online/offline status of the current student
    Status should be either 'online' or 'offline'
    """
    if status not in ['online', 'offline']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be either 'online' or 'offline'"
        )
    
    # Update the status in the database
    current_student.status = status
    current_student.last_active = get_vietnam_time().replace(tzinfo=None)
    db.commit()
    
    return {
        "message": f"Status updated to {status}",
        "user_id": current_student.user_id,
        "status": status,
        "last_active": current_student.last_active
    }
def calculate_band_score(total_score: float) -> float:
    # IELTS band score calculation logic
    # This is a simplified version - adjust according to actual IELTS scoring rules
    band_score = (total_score / 40) * 9
    return round(band_score * 2) / 2  # Rounds to nearest 0.5

@router.get("/exam/{exam_id}/start", response_model=Dict)
async def start_exam(
    exam_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    exam = db.query(Exam).filter(
        Exam.exam_id == exam_id,
        Exam.is_active == True
    ).first()
    
    if not exam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exam not found or not active"
        )

    sections = []
    exam_sections = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id
    ).order_by(ExamSection.order_number).all()

    for section in exam_sections:
        section_data = {
            "section_id": section.section_id,
            "section_type": section.section_type,
            "duration": section.duration,
            "total_marks": float(section.total_marks),
            "order_number": section.order_number,
            "questions": []
        }

        if section.section_type == 'reading':
            passages = db.query(ReadingPassage).filter(
                ReadingPassage.section_id == section.section_id
            ).all()
            section_data["passages"] = [
                {
                    "passage_id": p.passage_id,
                    "title": p.title.strip(),
                    "content": p.content.strip(),
                    "word_count": p.word_count
                } for p in passages
            ]

        elif section.section_type == 'listening':
            media = db.query(ListeningMedia).filter(
                ListeningMedia.section_id == section.section_id
            ).first()
            if media:
                section_data["media"] = {
                    "media_id": media.media_id,
                    "audio_filename": media.audio_filename,
                    "transcript": media.transcript.strip() if media.transcript else None,
                    "duration": media.duration
                }

        questions = db.query(Question).filter(
            Question.section_id == section.section_id
        ).order_by(Question.question_id).all()

        for question in questions:
            question_data = {
                "question_id": question.question_id,
                "question_text": question.question_text.strip(),
                "question_type": question.question_type.strip(),
                "marks": int(question.marks) if question.marks is not None else 0,
                "options": []
            }

            options = db.query(QuestionOption).filter(
                QuestionOption.question_id == question.question_id
            ).order_by(QuestionOption.option_id).all()
            
            question_data["options"] = [
                {
                    "option_id": opt.option_id,
                    "option_text": opt.option_text.strip()
                } for opt in options
            ]

            section_data["questions"].append(question_data)

        sections.append(section_data)

    return {
        "exam_id": exam.exam_id,
        "title": exam.title.strip(),
        "start_time": get_vietnam_time().replace(tzinfo=None),
        "sections": sections
    }


@router.post("/exam/{exam_id}/submit", response_model=Dict)
async def submit_exam_answers(
    exam_id: int,
    answers: Dict[str, str],
    request: Request,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # Import session checking functions
    from app.routes.admin.auth import check_multiple_sessions
    
    print(f"EXAM_SUBMIT - User {current_student.user_id} ({current_student.username}) submitting exam {exam_id}")
    
    # Check for multiple sessions before allowing submission
    current_session_token = request.headers.get("authorization", "").replace("Bearer ", "")
    
    print(f"EXAM_SUBMIT - Current session token: {current_session_token[:20]}...")
    
    if check_multiple_sessions(db, current_student.user_id, current_session_token):
        print(f"EXAM_SUBMIT - Multiple sessions detected for user {current_student.user_id}, blocking submission")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Multiple login sessions detected. Your exam submission has been cancelled and all devices have been logged out. If account sharing continues to be detected, the account will be permanently banned without admin intervention."
        )
    
    print(f"EXAM_SUBMIT - Device check passed, proceeding with submission")
    
    # Start a transaction with REPEATABLE READ isolation level to handle concurrency
    # This prevents dirty reads and non-repeatable reads
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    
    try:
        # Extract forecast parameters early to determine if this is a forecast submission
        forecast_part_str = request.query_params.get('forecast_part')
        is_reading_forecast = request.query_params.get('is_reading_forecast') == 'true'
        
        forecast_part = None
        is_forecast_submission = False
        
        if forecast_part_str:
            try:
                forecast_part = int(forecast_part_str)
                is_forecast_submission = True
            except ValueError:
                forecast_part = None
        
        # Verify exam exists - use a lock when checking to prevent race conditions
        exam = db.query(Exam).filter(
            Exam.exam_id == exam_id,
            Exam.is_active == True
        ).with_for_update().first()
        
        if not exam:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Exam not found or not active"
            )

        # Allow multiple attempts - no need to check for existing results
        # Each submission creates a new exam result for exam history

        # Get exam sections to determine the type
        exam_sections = db.query(ExamSection).filter(
            ExamSection.exam_id == exam_id
        ).all()
        
        # Check if this is a listening exam
        is_listening_exam = any(section.section_type == 'listening' for section in exam_sections)

        # Calculate attempt number - only count non-forecast attempts for full tests
        # For forecasts, count only forecast attempts for the same part
        if is_forecast_submission:
            existing_attempts = db.query(ExamResult).filter(
                ExamResult.user_id == current_student.user_id,
                ExamResult.exam_id == exam_id,
                ExamResult.is_forecast == True,
                ExamResult.forecast_part == forecast_part
            ).count()
        else:
            existing_attempts = db.query(ExamResult).filter(
                ExamResult.user_id == current_student.user_id,
                ExamResult.exam_id == exam_id,
                ExamResult.is_forecast.in_([False, None])  # Count only full test attempts
            ).count()

        # Create exam result record with proper forecast flags
        exam_result = ExamResult(
            user_id=current_student.user_id,
            exam_id=exam_id,
            completion_date=get_vietnam_time().replace(tzinfo=None),
            section_scores={},
            attempt_number=existing_attempts + 1,
            is_forecast=is_forecast_submission,  # Set database column
            forecast_part=forecast_part if is_forecast_submission else None  # Set database column
        )
        db.add(exam_result)
        db.flush()

        total_score = 0
        section_scores = {}

        # Process each answer
        for question_id, student_answer in answers.items():
            question = db.query(Question).filter(
                Question.question_id == int(question_id)
            ).first()
            
            if question:
                # Normalize student answer to lowercase
                normalized_student_answer = student_answer.lower().strip() if student_answer else ""
                
                # Check if correct answer has multiple options (separated by "or")
                correct_answers = [ans.lower().strip() for ans in question.correct_answer.split(" or ")] if question.correct_answer else []
                
                # Calculate score for this question - correct if student's answer matches any of the correct options
                is_correct = normalized_student_answer in correct_answers
                score = question.marks if is_correct else 0
                total_score += score

                # Store the answer based on exam type
                if is_listening_exam:
                    # Save to ListeningAnswer for listening exams
                    answer_record = ListeningAnswer(
                        user_id=current_student.user_id,
                        exam_id=exam_id,
                        result_id=exam_result.result_id,
                        question_id=question.question_id,
                        student_answer=student_answer,
                        score=score,
                        created_at=get_vietnam_time().replace(tzinfo=None)
                    )
                else:
                    # Use StudentAnswer for other exam types
                    answer_record = StudentAnswer(
                        result_id=exam_result.result_id,
                        question_id=question.question_id,
                        student_answer=student_answer,
                        score=score
                    )
                
                db.add(answer_record)

                # Aggregate section scores
                section_id = question.section_id
                if section_id not in section_scores:
                    section_scores[section_id] = {"earned": 0, "total": 0}
                section_scores[section_id]["earned"] += score
                section_scores[section_id]["total"] += question.marks

        exam_result.total_score = total_score
        exam_result.section_scores = section_scores
        
        db.commit()

        try:
            forecast_part_str = request.query_params.get('forecast_part')
            if is_listening_exam and forecast_part_str:
                try:
                    forecast_part = int(forecast_part_str)
                except ValueError:
                    forecast_part = None

                if forecast_part and 1 <= forecast_part <= 4:
                    part_section = db.query(ExamSection).filter(
                        ExamSection.exam_id == exam_id,
                        ExamSection.section_type == 'listening',
                        ExamSection.order_number == forecast_part
                    ).first()

                    if part_section:
                        part_total = db.query(Question).filter(
                            Question.section_id == part_section.section_id,
                            Question.question_type != 'main_text'
                        ).with_entities(Question.marks).all()
                        total_marks_part = sum([q.marks or 0 for q in part_total])

                        part_answers = db.query(ListeningAnswer).join(Question, ListeningAnswer.question_id == Question.question_id)\
                            .filter(
                                ListeningAnswer.result_id == exam_result.result_id,
                                Question.section_id == part_section.section_id
                            ).all()
                        earned_marks_part = sum([a.score or 0 for a in part_answers])

                        attempt_entry = {
                            "result_id": exam_result.result_id,
                            "completion_date": exam_result.completion_date.isoformat() if exam_result.completion_date else None,
                            "part_number": forecast_part,
                            "score_earned": earned_marks_part,
                            "score_total": total_marks_part
                        }

                        cache_key = f"forecast_attempts:{current_student.user_id}:{exam_id}:{forecast_part}"
                        existing_attempts = await cache.get(cache_key) or []
                        existing_attempts.append(attempt_entry)
                        await cache.set(cache_key, existing_attempts, ttl=31536000)
                        # Mark this exam_result as forecast to separate it from full tests
                        await cache.set(f"forecast_result:{exam_result.result_id}", True, ttl=31536000)
        except Exception:
            pass

        return {
            "result_id": exam_result.result_id,
            "total_score": total_score,
            "section_scores": section_scores,
            "completion_date": exam_result.completion_date
        }
    except Exception as e:
        # Rollback the transaction in case of any error
        db.rollback()
        
        # Log the error for debugging
        print(f"Error submitting exam: {str(e)}")
        
        # Return a user-friendly error message
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while submitting your exam. Please try again."
        )

@router.get("/exam-result/{result_id}", response_model=Dict)
async def get_exam_result_details(
    result_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # Get exam result with validation
    exam_result = db.query(ExamResult).filter(
        ExamResult.result_id == result_id,
        ExamResult.user_id == current_student.user_id
    ).first()
    
    if not exam_result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exam result not found"
        )

    # Get exam sections to determine the type
    exam_sections = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_result.exam_id
    ).all()
    
    # Check if this is a listening exam
    is_listening_exam = any(section.section_type == 'listening' for section in exam_sections)

    detailed_answers = []
    
    if is_listening_exam:
        # Get all questions for this listening exam, excluding main_text questions
        all_questions = db.query(Question)\
            .join(ExamSection, Question.section_id == ExamSection.section_id)\
            .filter(
                ExamSection.exam_id == exam_result.exam_id,
                ExamSection.section_type == 'listening',
                Question.question_type != 'main_text'  # Exclude main_text questions
            )\
            .order_by(Question.question_id)\
            .all()
        
        # Create a dictionary of answered questions for quick lookup
        answered_questions = {}
        listening_answers = db.query(ListeningAnswer).filter(
            ListeningAnswer.result_id == exam_result.result_id
        ).all()
        
        for answer in listening_answers:
            answered_questions[answer.question_id] = answer
        
        # Process all questions (1-40) with their evaluation status
        for i, question in enumerate(all_questions, 1):
            answer = answered_questions.get(question.question_id)
            
            if answer:
                # Question was answered
                evaluation = "correct" if answer.score > 0 else "wrong"
                student_answer = answer.student_answer
                score = answer.score
            else:
                # Question was not answered (blank)
                evaluation = "blank"
                student_answer = ""
                score = 0
            
            detailed_answers.append({
                "question_number": i,  # Assign sequential number from 1-40
                "question_id": question.question_id,
                "question_type": question.question_type,  # Include question type for frontend evaluation
                "question_text": question.question_text,
                "student_answer": student_answer,
                "correct_answer": question.correct_answer,
                "explanation": question.explanation,  # Include explanation in response
                "locate": question.locate,
                "score": score,
                "max_marks": question.marks,
                "evaluation": evaluation
            })
    else:
        # Get detailed answers from StudentAnswer table for other exam types
        student_answers = db.query(StudentAnswer).filter(
            StudentAnswer.result_id == result_id
        ).all()
        
        for answer in student_answers:
            question = answer.question
            evaluation = "correct" if answer.score > 0 else "wrong"
            
            detailed_answers.append({
                "question_id": question.question_id,
                "question_number": question.question_number,  # Include question_number for reading exams
                "question_type": question.question_type,  # Include question type for frontend evaluation
                "question_text": question.question_text,
                "student_answer": answer.student_answer,
                "correct_answer": question.correct_answer,
                "explanation": question.explanation,  # Include explanation in response
                "locate": question.locate,
                "score": answer.score,
                "max_marks": question.marks,
                "evaluation": evaluation
            })

    return {
        "exam_id": exam_result.exam_id,
        "completion_date": exam_result.completion_date,
        "total_score": exam_result.total_score,
        "section_scores": exam_result.section_scores,
        "detailed_answers": detailed_answers
    }
@router.get("/my-exam-history", response_model=List[dict])
async def get_student_exam_history(
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    from sqlalchemy.sql import func
    
    # 1) Get all exam results with exam titles (ONE query via joinedload)
    exam_results = db.query(ExamResult).options(
        joinedload(ExamResult.exam)
    ).filter(
        ExamResult.user_id == current_student.user_id
    ).order_by(ExamResult.completion_date.desc()).all()
    
    if not exam_results:
        return []
    
    # 2) Batch load first section type for all unique exam_ids (ONE query)
    all_exam_ids = list(set(r.exam_id for r in exam_results))
    section_type_rows = db.query(
        ExamSection.exam_id,
        func.min(ExamSection.section_type).label('section_type')
    ).filter(
        ExamSection.exam_id.in_(all_exam_ids)
    ).group_by(ExamSection.exam_id).all()
    exam_type_map = {eid: stype for eid, stype in section_type_rows}
    
    # 3) Batch load question counts for forecast sections (ONE query)
    forecast_results = [(r.exam_id, r.forecast_part) for r in exam_results 
                        if r.is_forecast and r.forecast_part]
    question_count_map = {}  # (exam_id, order_number) -> count
    if forecast_results:
        # Get all relevant sections first
        forecast_exam_ids = list(set(eid for eid, _ in forecast_results))
        forecast_parts = list(set(part for _, part in forecast_results))
        
        section_rows = db.query(ExamSection.exam_id, ExamSection.order_number, ExamSection.section_id).filter(
            ExamSection.exam_id.in_(forecast_exam_ids),
            ExamSection.order_number.in_(forecast_parts)
        ).all()
        section_id_map = {(row.exam_id, row.order_number): row.section_id for row in section_rows}
        
        section_ids = [sid for sid in section_id_map.values()]
        if section_ids:
            q_count_rows = db.query(
                Question.section_id,
                func.count(Question.question_id).label('cnt')
            ).filter(
                Question.section_id.in_(section_ids),
                Question.question_type != 'main_text'
            ).group_by(Question.section_id).all()
            sid_to_count = {sid: cnt for sid, cnt in q_count_rows}
            
            for (eid, order_num), sid in section_id_map.items():
                question_count_map[(eid, order_num)] = sid_to_count.get(sid, 40)
    
    # Build response using pre-loaded data
    result_list = []
    for result in exam_results:
        exam_type = exam_type_map.get(result.exam_id, "reading")
        
        total_questions = 40  # Default for full tests
        if result.is_forecast and result.forecast_part:
            total_questions = question_count_map.get(
                (result.exam_id, result.forecast_part), 40
            )
        
        result_list.append({
            "result_id": result.result_id,
            "exam_id": result.exam_id,
            "exam_title": result.exam.title,
            "total_score": result.total_score,
            "total_questions": total_questions,
            "completion_date": result.completion_date,
            "section_scores": result.section_scores,
            "attempt_number": result.attempt_number if hasattr(result, 'attempt_number') and result.attempt_number else 1,
            "exam_type": exam_type,
            "is_forecast": bool(result.is_forecast),
            "forecast_part": result.forecast_part if result.is_forecast else None,
            "part_number": result.forecast_part if result.is_forecast else None
        })
    
    return result_list


@router.get("/my-exam-result/{result_id}", response_model=Dict)
async def get_student_exam_detail(
    result_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    exam_result = db.query(ExamResult).filter(
        ExamResult.result_id == result_id,
        ExamResult.user_id == current_student.user_id
    ).first()
    
    if not exam_result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exam result not found"
        )

    answers = db.query(StudentAnswer).filter(
        StudentAnswer.result_id == result_id
    ).all()

    sections = {}
    for answer in answers:
        question = answer.question
        section = question.section
        
        if section.section_id not in sections:
            sections[section.section_id] = {
                "section_type": section.section_type,
                "duration": section.duration,
                "total_marks": section.total_marks,
                "description": section.description,
                "answers": []
            }
            
        sections[section.section_id]["answers"].append({
            "question_id": question.question_id,
            "question_text": question.question_text,
            "student_answer": answer.student_answer,
            "correct_answer": question.correct_answer,
            "score": answer.score,
            "max_marks": question.marks
        })

    return {
        "exam_id": exam_result.exam_id,
        "exam_title": exam_result.exam.title,
        "completion_date": exam_result.completion_date,
        "total_score": exam_result.total_score,
        "section_scores": exam_result.section_scores,
        "sections": sections
    }

@router.get("/listening/exam/{exam_id}/part-descriptions", response_model=dict)
async def get_listening_part_descriptions(
    exam_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # Verify exam exists and is a listening exam
    exam = db.query(Exam).filter(
        Exam.exam_id == exam_id,
        Exam.is_active == True
    ).first()
    
    if not exam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exam not found or not active"
        )
    
    # Check if this is a listening exam
    listening_sections = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.section_type == 'listening'
    ).order_by(ExamSection.order_number).all()
    
    if not listening_sections:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This is not a listening exam"
        )
    
    # Check if user has access to this exam
    has_access = check_exam_access(db, exam_id, current_student)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this exam"
        )
    
    # Initialize descriptions dictionary
    descriptions = {
        "exam_id": exam_id,
        "title": exam.title,
        "description": exam.description,
        "part1_description": None,
        "part2_description": None,
        "part3_description": None,
        "part4_description": None,
        "parts_count": len(listening_sections)
    }
    
    # Get descriptions from each section based on order_number
    for section in listening_sections:
        if 1 <= section.order_number <= 4:
            descriptions[f"part{section.order_number}_description"] = section.description
    
    return descriptions
    
    
@router.get("/writing/tasks", response_model=List[dict])
async def get_writing_tasks(
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # 1) Get all active exams with essay sections (ONE query)
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'essay'
    ).distinct().order_by(Exam.exam_id).all()

    if not exams:
        return []

    exam_ids = [e.exam_id for e in exams]

    # 2) Batch load access types (ONE query)
    all_access = db.query(ExamAccessType).filter(
        ExamAccessType.exam_id.in_(exam_ids)
    ).all()
    access_by_exam = {}
    for a in all_access:
        access_by_exam.setdefault(a.exam_id, []).append(a.access_type)

    # 3) Batch load all writing tasks (ONE query)
    all_tasks = db.query(WritingTask).filter(
        WritingTask.test_id.in_(exam_ids)
    ).order_by(WritingTask.part_number).all()
    tasks_by_exam = {}
    for t in all_tasks:
        tasks_by_exam.setdefault(t.test_id, []).append(t)

    # 4) Batch load all user's writing answers (ONE query)
    all_task_ids = [t.task_id for t in all_tasks]
    user_answers_set = set()
    if all_task_ids:
        user_answer_rows = db.query(WritingAnswer.task_id).filter(
            WritingAnswer.task_id.in_(all_task_ids),
            WritingAnswer.user_id == current_student.user_id
        ).all()
        user_answers_set = set(row[0] for row in user_answer_rows)

    # Determine allowed access types
    allowed_types = []
    if current_student.role == 'student':
        allowed_types = ['student']
    elif current_student.role == 'customer':
        if current_student.is_vip:
            allowed_types = ['no vip', 'vip']
        else:
            allowed_types = ['no vip']

    # Build response using pre-loaded data
    exam_details = []
    for exam in exams:
        exam_types = access_by_exam.get(exam.exam_id, [])
        if not any(at in allowed_types for at in exam_types):
            continue
        
        tasks = tasks_by_exam.get(exam.exam_id, [])
        if not tasks:
            continue
        
        answered_count = sum(1 for t in tasks if t.task_id in user_answers_set)
        
        exam_details.append({
            "test_id": exam.exam_id,
            "title": exam.title,
            "created_at": exam.created_at,
            "is_completed": answered_count == len(tasks),
            "parts": [{
                "task_id": task.task_id,
                "part_number": task.part_number,
                "task_type": task.task_type,
                "instructions": task.instructions,
                "sample_essay": getattr(task, 'sample_essay', None),
                "word_limit": task.word_limit,
                "total_marks": task.total_marks,
                "duration": task.duration
            } for task in tasks]
        })

    return exam_details


@router.get("/writing/tasks/{task_id}", response_model=dict)
async def get_writing_task_detail(
    task_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    task = db.query(WritingTask).filter(
        WritingTask.task_id == task_id
    ).first()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Writing task not found"
        )
    
    # Get student's previous answer if exists using WritingAnswer
    previous_answer = db.query(WritingAnswer).filter(
        WritingAnswer.task_id == task_id,
        WritingAnswer.user_id == current_student.user_id
    ).first()

    return {
        "task_id": task.task_id,
        "part_number": task.part_number,
        "task_type": task.task_type,
        "instructions": task.instructions,
        "sample_essay": getattr(task, 'sample_essay', None),
        "word_limit": task.word_limit,
        "total_marks": task.total_marks,
        "duration": task.duration,
        "previous_answer": {
            "answer_text": previous_answer.answer_text,
            "score": previous_answer.score,
            "created_at": previous_answer.created_at,
            "updated_at": previous_answer.updated_at
        } if previous_answer else None
    }

# Add this new endpoint to get all writing answers for a test
@router.get("/writing/test/{test_id}/answers", response_model=dict)
async def get_writing_test_answers(
    test_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # Get all tasks and answers for this test
    answers = db.query(WritingTask, WritingAnswer)\
        .outerjoin(WritingAnswer, and_(
            WritingAnswer.task_id == WritingTask.task_id,
            WritingAnswer.user_id == current_student.user_id
        ))\
        .filter(WritingTask.test_id == test_id)\
        .order_by(WritingTask.part_number)\
        .all()
    
    if not answers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No writing tasks found for this test"
        )
    
    return {
        "test_id": test_id,
        "parts": [{
            "task_id": task.task_id,
            "part_number": task.part_number,
            "task_type": task.task_type,
            "instructions": task.instructions,
            "word_limit": task.word_limit,
            "answer": {
                "answer_text": answer.answer_text if answer else None,
                "score": answer.score if answer else None,
                "created_at": answer.created_at if answer else None,
                "updated_at": answer.updated_at if answer else None
            }
        } for task, answer in answers]
    }
@router.post("/writing/test/{test_id}/submit", response_model=dict)
async def submit_complete_writing_test(
    test_id: int,
    answers: WritingTestSubmit,
    request: Request,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # Import session checking functions
    from app.routes.admin.auth import check_multiple_sessions
    
    # Check for multiple sessions before allowing submission
    current_session_token = request.headers.get("authorization", "").replace("Bearer ", "")
    
    if check_multiple_sessions(db, current_student.user_id, current_session_token):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Multiple login sessions detected. Please log out from other devices before submitting your exam."
        )
    
    # Get both tasks for this test
    tasks = db.query(WritingTask).filter(
        WritingTask.test_id == test_id
    ).order_by(WritingTask.part_number).all()
    
    if len(tasks) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Writing test must have exactly two parts"
        )

    # Process both parts
    submissions = []
    for task in tasks:
        answer_text = answers.part1_answer if task.part_number == 1 else answers.part2_answer
        
        # Create or update answer
        writing_answer = db.query(WritingAnswer).filter(
            WritingAnswer.task_id == task.task_id,
            WritingAnswer.user_id == current_student.user_id
        ).first()

        if writing_answer:
            writing_answer.answer_text = answer_text
            writing_answer.updated_at = get_vietnam_time().replace(tzinfo=None)
        else:
            writing_answer = WritingAnswer(
                task_id=task.task_id,
                user_id=current_student.user_id,
                answer_text=answer_text,
                score=0,
                created_at=get_vietnam_time().replace(tzinfo=None),
                updated_at=get_vietnam_time().replace(tzinfo=None)
            )
            db.add(writing_answer)
        
        submissions.append({
            "part_number": task.part_number,
            "word_count": len(answer_text.split()),
            "task_type": task.task_type
        })

    db.commit()

    return {
        "message": "Writing test submitted successfully",
        "test_id": test_id,
        "submissions": submissions
    }
@router.post("/writing/tasks/{task_id}/save-draft", response_model=dict)
async def submit_writing_answer(
    task_id: int,
    answer_data: WritingAnswerSubmit,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    task = db.query(WritingTask).filter(
        WritingTask.task_id == task_id
    ).first()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Writing task not found"
        )

    # Save current answer
    writing_answer = db.query(WritingAnswer).filter(
        WritingAnswer.task_id == task_id,
        WritingAnswer.user_id == current_student.user_id
    ).first()

    if writing_answer:
        writing_answer.answer_text = answer_data.answer_text
        writing_answer.updated_at = get_vietnam_time().replace(tzinfo=None)
    else:
        writing_answer = WritingAnswer(
            task_id=task_id,
            user_id=current_student.user_id,
            answer_text=answer_data.answer_text,
            score=0,
            created_at=get_vietnam_time().replace(tzinfo=None),
            updated_at=get_vietnam_time().replace(tzinfo=None)
        )
        db.add(writing_answer)
        db.flush()

    # Get other part's status
    other_part = db.query(WritingTask, WritingAnswer).outerjoin(
        WritingAnswer,
        and_(
            WritingAnswer.task_id == WritingTask.task_id,
            WritingAnswer.user_id == current_student.user_id
        )
    ).filter(
        WritingTask.test_id == task.test_id,
        WritingTask.task_id != task_id
    ).first()

    db.commit()

    return {
        "message": "Writing answer submitted successfully",
        "task_id": task_id,
        "part_number": task.part_number,
        "word_count": len(answer_data.answer_text.split()),
        "answer_text": writing_answer.answer_text,
        "other_part": {
            "task_id": other_part[0].task_id if other_part else None,
            "part_number": other_part[0].part_number if other_part else None,
            "submitted": bool(other_part[1]) if other_part else False
        } if other_part else None
    }

