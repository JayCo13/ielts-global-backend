from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Exam, ExamSection, Question, QuestionOption, ReadingPassage, QuestionGroup, ExamResult, StudentAnswer, ExamAccessType
from app.routes.admin.auth import get_current_student
from typing import List, Dict
from sqlalchemy.sql import func
from datetime import datetime
from pydantic import BaseModel
from app.utils.redis_cache import cache, get_reading_test_cache_key
from app.utils.datetime_utils import get_vietnam_time
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

class ReadingExamSubmission(BaseModel):
    answers: Dict[str, str]  # question_id -> student_answer

@router.get("/reading-tests", response_model=List[dict])
async def get_available_reading_tests(
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Get all available reading tests for students"""
    from sqlalchemy import and_
    
    # 1) Get all active exams with reading sections (ONE query)
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'reading'
    ).distinct().order_by(Exam.exam_id).all()

    if not exams:
        return []

    exam_ids = [e.exam_id for e in exams]

    # 2) Batch load all access types (ONE query)
    all_access = db.query(ExamAccessType).filter(
        ExamAccessType.exam_id.in_(exam_ids)
    ).all()
    access_by_exam = {}
    for a in all_access:
        access_by_exam.setdefault(a.exam_id, []).append(a.access_type)

    # 3) Batch load all reading sections (ONE query)
    all_sections = db.query(ExamSection).filter(
        ExamSection.exam_id.in_(exam_ids),
        ExamSection.section_type == 'reading'
    ).order_by(ExamSection.order_number).all()
    sections_by_exam = {}
    for s in all_sections:
        sections_by_exam.setdefault(s.exam_id, []).append(s)

    # 4) Batch load latest results per exam (ONE query)
    latest_results_sub = db.query(
        ExamResult.exam_id,
        func.max(ExamResult.completion_date).label('max_date')
    ).filter(
        ExamResult.exam_id.in_(exam_ids),
        ExamResult.user_id == current_student.user_id
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


@router.get("/reading-test/{exam_id}/description", response_model=dict)
async def get_reading_test_description(
    exam_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Get only the description of a specific reading test"""
    
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Reading test not found")
    
    return {
        "exam_id": exam.exam_id,
        "title": exam.title,
        "description": exam.description
    }

@router.delete("/reading-test/{exam_id}/retake", response_model=dict)
async def retake_reading_exam(
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
        "message": f"Ready for reading exam attempt #{existing_attempts + 1}. Previous attempts are preserved in your exam history.",
        "exam_id": exam_id,
        "attempt_number": existing_attempts + 1,
        "previous_attempts": existing_attempts
    }
@router.get("/reading-test/{exam_id}", response_model=Dict)
async def get_reading_test(
    exam_id: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Get details of a specific reading test with caching"""
    # Try to get from cache first
    cache_key = get_reading_test_cache_key(exam_id)
    cached_result = await cache.get(cache_key)
    
    if cached_result:
        logger.info(f"Reading test {exam_id} served from cache")
        return cached_result
    
    exam = db.query(Exam).filter(
        Exam.exam_id == exam_id,
        Exam.is_active == True
    ).first()
    
    if not exam:
        raise HTTPException(status_code=404, detail="Reading test not found")
    
    sections = db.query(ExamSection)\
        .filter(
            ExamSection.exam_id == exam_id,
            ExamSection.section_type == 'reading'
        )\
        .order_by(ExamSection.order_number)\
        .all()
    
    section_details = []
    for section in sections:
        section_data = {
            "section_id": section.section_id,
            "section_type": "reading",
            "duration": section.duration,
            "total_marks": float(section.total_marks),
            "order_number": section.order_number,
            "description": section.description,
            "questions": []
        }

        # Get passage
        passages = db.query(ReadingPassage)\
            .filter(ReadingPassage.section_id == section.section_id)\
            .all()
        section_data["passages"] = [
            {
                "passage_id": p.passage_id,
                "title": p.title.strip(),
                "content": p.content.strip(),
                "word_count": p.word_count
            } for p in passages
        ]

        # Get all questions including main_text
        questions = db.query(Question)\
            .filter(Question.section_id == section.section_id)\
            .order_by(Question.question_id)\
            .all()

        for question in questions:
            question_data = {
                "question_id": question.question_id,
                "question_text": question.question_text.strip(),
                "question_type": question.question_type.strip(),
                "marks": int(question.marks) if question.marks is not None else 0,
                "question_number": question.question_number if question.question_type != 'main_text' else None,
                "explanation": question.explanation,
                "locate": question.locate,
                "additional_data": question.additional_data,
                "options": []
            }

            options = db.query(QuestionOption)\
                .filter(QuestionOption.question_id == question.question_id)\
                .order_by(QuestionOption.option_id)\
                .all()
            
            question_data["options"] = [
                {
                    "option_id": opt.option_id,
                    "option_text": opt.option_text.strip()
                } for opt in options
            ]

            section_data["questions"].append(question_data)

        section_details.append(section_data)
    
    result = {
        "exam_id": exam.exam_id,
        "title": exam.title.strip(),
        "created_at": exam.created_at,
        "sections": section_details
    }
    
    # Cache the result for 2 hours (7200 seconds)
    await cache.set(cache_key, result, ttl=7200)
    logger.info(f"Reading test {exam_id} cached successfully")
    
    return result

@router.post("/reading-test/{exam_id}/submit", response_model=Dict)
async def submit_reading_exam(
    exam_id: int,
    submission: ReadingExamSubmission,
    request: Request,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Submit answers for a reading exam"""
    # Import device checking functions
    from app.routes.admin.auth import generate_device_id, check_multiple_sessions, get_current_session
    
    # Check for multiple sessions before allowing submission
    user_agent = request.headers.get("user-agent", "")
    client_ip = request.client.host
    current_device_id = generate_device_id(user_agent, client_ip)
    
    # Get current session token from authorization header
    current_session_token = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        current_session_token = auth_header.split(" ")[1]
    
    if check_multiple_sessions(db, current_student.user_id, current_session_token):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Multiple active sessions detected. Please logout from other devices/browsers before submitting the exam."
        )
    
    # Start a transaction with REPEATABLE READ isolation level to handle concurrency
    # This prevents dirty reads and non-repeatable reads
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    
    try:
        # Verify exam exists and is active - use a lock when checking to prevent race conditions
        exam = db.query(Exam).filter(
            Exam.exam_id == exam_id,
            Exam.is_active == True
        ).with_for_update().first()
        
        if not exam:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Reading exam not found or not active"
            )

        # Allow multiple attempts - no need to check for existing results
        # Each submission creates a new exam result for exam history

        # Verify this is a reading exam
        exam_sections = db.query(ExamSection).filter(
            ExamSection.exam_id == exam_id,
            ExamSection.section_type == 'reading'
        ).all()
        
        if not exam_sections:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This is not a reading exam"
            )

        # Determine if this is a forecast submission EARLY
        forecast_part_str = request.query_params.get('forecast_part')
        forecast_part = None
        is_forecast_submission = False
        if forecast_part_str:
            try:
                fp = int(forecast_part_str)
                if 1 <= fp <= 3:
                    forecast_part = fp
                    is_forecast_submission = True
            except ValueError:
                forecast_part = None

        # Calculate attempt number - separate counting for forecasts vs full tests
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
                ExamResult.is_forecast.in_([False, None])
            ).count()

        # Create exam result record with forecast flags
        exam_result = ExamResult(
            user_id=current_student.user_id,
            exam_id=exam_id,
            completion_date=get_vietnam_time().replace(tzinfo=None),
            section_scores={},
            attempt_number=existing_attempts + 1,
            is_forecast=is_forecast_submission,
            forecast_part=forecast_part if is_forecast_submission else None
        )
        db.add(exam_result)
        db.flush()

        total_score = 0
        section_scores = {}

        # Get all questions for this reading exam ordered by question_number
        all_questions = db.query(Question)\
            .join(ExamSection, Question.section_id == ExamSection.section_id)\
            .filter(
                ExamSection.exam_id == exam_id,
                ExamSection.section_type == 'reading',
                Question.question_type != 'main_text'  # Exclude main_text questions
            )\
            .order_by(Question.question_number)\
            .all()
        
        # Create mappings for efficient lookup
        questions_by_number = {str(q.question_number): q for q in all_questions}
        
        # Define part ranges
        part_ranges = {
            1: (1, 13),   # Part 1: questions 1-13
            2: (14, 26),  # Part 2: questions 14-26
            3: (27, 40)   # Part 3: questions 27-40
        }
        
        part_scores = {1: {"earned": 0, "total": 0}, 2: {"earned": 0, "total": 0}, 3: {"earned": 0, "total": 0}}

        # Choose question range based on forecast_part or full exam
        if forecast_part:
            range_start, range_end = part_ranges[forecast_part]
        else:
            range_start, range_end = 1, 40

        # Validate questions exist for the selected range
        expected_question_numbers = set(range(range_start, range_end + 1))
        actual_question_numbers = {
            q.question_number for q in all_questions
            if q.question_number is not None and range_start <= q.question_number <= range_end
        }
        
        # Debug logging for checkbox/validation issues
        logger.info(f"SUBMIT_DEBUG - Exam {exam_id}: Expected question numbers: {sorted(expected_question_numbers)}")
        logger.info(f"SUBMIT_DEBUG - Exam {exam_id}: Actual question numbers found: {sorted(actual_question_numbers)}")
        missing = expected_question_numbers - actual_question_numbers
        extra = actual_question_numbers - expected_question_numbers
        if missing:
            logger.warning(f"SUBMIT_DEBUG - Exam {exam_id}: MISSING question numbers: {sorted(missing)}")
        if extra:
            logger.warning(f"SUBMIT_DEBUG - Exam {exam_id}: EXTRA question numbers: {sorted(extra)}")
        
        if actual_question_numbers != expected_question_numbers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Reading exam missing required questions for the selected range. "
                    f"Expected: {sorted(expected_question_numbers)}, Found: {sorted(actual_question_numbers)}, "
                    f"Missing: {sorted(missing)}"
                )
            )

        # Process answers for chosen range
        for question_number in range(range_start, range_end + 1):
            str_question_number = str(question_number)
            question = questions_by_number.get(str_question_number)
            if not question:
                continue
                
            # Get student answer using question number instead of ID
            student_answer = submission.answers.get(str_question_number, "")
            
            # Normalize student answer to lowercase
            normalized_student_answer = student_answer.lower().strip() if student_answer else ""
            
            # Check if correct answer has multiple options (separated by "or")
            correct_answers = [ans.lower().strip() for ans in question.correct_answer.split(" or ")] if question.correct_answer else []
            
            # Calculate score for this question - correct if student's answer matches any of the correct options
            is_correct = normalized_student_answer in correct_answers
            marks_value = int(question.marks) if question.marks is not None else 1
            score = marks_value if is_correct else 0
            total_score += score

            # Store the answer
            answer_record = StudentAnswer(
                result_id=exam_result.result_id,
                question_id=question.question_id,
                student_answer=student_answer,
                score=score
            )
            db.add(answer_record)

            # Determine which part this question belongs to and aggregate scores
            for part_num, (start, end) in part_ranges.items():
                if start <= question_number <= end:
                    part_scores[part_num]["earned"] += score
                    part_scores[part_num]["total"] += marks_value
                    break
            
            # Also aggregate section scores for backward compatibility
            section_id = question.section_id
            if section_id not in section_scores:
                section_scores[section_id] = {"earned": 0, "total": 0}
            section_scores[section_id]["earned"] += score
            # Ensure None marks do not break aggregation
            marks_value = int(question.marks) if question.marks is not None else 1
            section_scores[section_id]["total"] += marks_value

        # Update exam result with total score and section scores
        exam_result.total_score = total_score
        exam_result.section_scores = section_scores
        
        # Commit the transaction
        db.commit()

        # If forecast submission, cache attempt details and mark result
        try:
            if forecast_part:
                attempt_entry = {
                    "result_id": exam_result.result_id,
                    "completion_date": exam_result.completion_date.isoformat() if exam_result.completion_date else None,
                    "part_number": forecast_part,
                    "score_earned": part_scores[forecast_part]["earned"],
                    "score_total": part_scores[forecast_part]["total"]
                }

                cache_key = f"forecast_attempts:{current_student.user_id}:{exam_id}:{forecast_part}"
                existing_attempts = await cache.get(cache_key) or []
                existing_attempts.append(attempt_entry)
                await cache.set(cache_key, existing_attempts, ttl=31536000)
                await cache.set(f"forecast_result:{exam_result.result_id}", True, ttl=31536000)
        except Exception:
            pass

        return {
            "result_id": exam_result.result_id,
            "total_score": total_score,
            "section_scores": section_scores,
            "part_scores": part_scores,
            "completion_date": exam_result.completion_date,
            "message": "Reading exam submitted successfully"
        }
    except HTTPException as e:
        # Allow intended HTTP errors (e.g., 400 validation) to propagate
        db.rollback()
        raise e
    except Exception as e:
        # Rollback the transaction in case of any error
        db.rollback()
        
        # Log the error for debugging
        print(f"Error submitting reading exam: {str(e)}")
        
        # Re-raise the exception with a user-friendly message
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while submitting your exam. Please try again."
        )
@router.get("/forecasts", response_model=List[dict])
async def get_reading_forecasts(
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'reading'
    ).distinct().order_by(Exam.exam_id).all()

    result = []
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
            ExamSection.section_type == 'reading'
        ).order_by(ExamSection.order_number).all()

        from sqlalchemy.sql import func
        forecast_sections = [s for s in sections if getattr(s, 'is_forecast', False)]
        if not forecast_sections:
            continue
        section_ids = [s.section_id for s in forecast_sections]
        expected_rows = db.query(
            Question.section_id,
            func.count(Question.question_id).label('expected_cnt')
        ).filter(
            Question.section_id.in_(section_ids),
            Question.question_type != 'main_text'
        ).group_by(Question.section_id).all()
        expected_map = {sid: cnt for sid, cnt in expected_rows}

        res_ids = [r.result_id for r in db.query(ExamResult.result_id).filter(
            ExamResult.user_id == current_student.user_id,
            ExamResult.exam_id == exam.exam_id
        ).all()]

        attempts_by_section = {}
        if res_ids:
            rows = db.query(
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
            for rid, sid, cnt in rows:
                attempts_by_section.setdefault(sid, []).append((rid, cnt))

        forecast_parts = []
        for s in forecast_sections:
            expected = expected_map.get(s.section_id, 0)
            candidates = attempts_by_section.get(s.section_id, [])
            attempts_count = sum(1 for _, cnt in candidates if cnt == expected)
            forecast_parts.append({
                'part_number': s.order_number,
                'forecast_title': getattr(s, 'forecast_title', None),
                'completed': attempts_count > 0,
                'attempts_count': attempts_count,
                'is_recommended': bool(getattr(s, 'is_recommended', False)),
                'question_types': getattr(s, 'question_types', None) or []
            })
        # forecast_sections already ensured
        result.append({
            'exam_id': exam.exam_id,
            'exam_title': exam.title,
            'parts': forecast_parts
        })
    return result

@router.get("/forecast-history/{exam_id}/{part_number}", response_model=List[dict])
async def get_reading_forecast_history(
    exam_id: int,
    part_number: int,
    current_student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    # Get section for this part to calculate total marks
    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.section_type == 'reading',
        ExamSection.order_number == part_number
    ).first()
    if not section:
        return []

    from sqlalchemy.sql import func
    
    # Use database columns to filter forecast results directly
    forecast_results = db.query(ExamResult).filter(
        ExamResult.user_id == current_student.user_id,
        ExamResult.exam_id == exam_id,
        ExamResult.is_forecast == True,
        ExamResult.forecast_part == part_number
    ).order_by(ExamResult.completion_date.desc()).all()

    # Compute total marks for this part (excluding main_text questions)
    total_marks = db.query(func.sum(Question.marks))\
        .filter(
            Question.section_id == section.section_id,
            Question.question_type != 'main_text'
        )\
        .scalar() or 0

    # Calculate earned scores for each forecast result
    attempts = []
    for result in forecast_results:
        # Get earned score from StudentAnswer
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
