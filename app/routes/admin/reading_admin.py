from pydoc import locate
from fastapi import APIRouter, Depends, HTTPException, status, Form
from sqlalchemy.orm import Session
import json
from app.database import get_db
from app.models.models import StudentAnswer, Exam, ExamSection, Question, QuestionOption, ReadingPassage, QuestionGroup, User
from app.routes.admin.auth import get_current_admin
from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel
from sqlalchemy.sql import func
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()

# Request models
class ReadingTestInit(BaseModel):
    title: str
    description: str
    duration: int = 60  # Default 60 minutes
    total_marks: float = 40.0  # Default 40 marks
    part1_description: Optional[str] = None
    part2_description: Optional[str] = None
    part3_description: Optional[str] = None

class PassageCreate(BaseModel):
    content: str
    title: str

class ReadingQuestionCreate(BaseModel):
    question_text: str
    question_type: str  # 'multiple_choice', 'true_false', 'matching', 'fill_blank', etc.
    correct_answer: str
    marks: int
    question_number: int  # The question number in the test (e.g., 1, 2, 3...)
    options: Optional[List[dict]] = None  # For multiple choice questions
    explanation: Optional[str] = None  # Explanation for the correct answer
    locate: Optional[str] = None  # Location of the question in the passage

class QuestionGroupCreate(BaseModel):
    instruction: str
    question_range: str  # e.g., "1-6", "7-13"
    group_type: str  # e.g., "true_false_ng", "fill_blank"
    order_number: int
    questions: List[ReadingQuestionCreate]

class ReadingPartUpdate(BaseModel):
    passage: PassageCreate
    question_groups: List[QuestionGroupCreate]

class ReadingTestTitleUpdate(BaseModel):
    title: str


class ReadingForecastUpdate(BaseModel):
    part_number: int
    is_forecast: bool
    forecast_title: Optional[str] = None
    is_recommended: Optional[bool] = None


# Initialize a new reading test
@router.put("/reading-test/{exam_id}/title", response_model=dict)
async def update_reading_test_title(
    exam_id: int,
    title_data: ReadingTestTitleUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update the title of a reading test"""
    
    # Check if exam exists
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Reading test not found")
    
    # Check if new title already exists (excluding current exam)
    existing_exam = db.query(Exam).filter(Exam.title == title_data.title, Exam.exam_id != exam_id).first()
    if existing_exam:
        raise HTTPException(status_code=400, detail="An exam with this title already exists")
    
    exam.title = title_data.title
    db.add(exam)
    db.commit()
    db.refresh(exam)
    
    return {"message": "Reading test title updated successfully", "exam_id": exam.exam_id, "new_title": exam.title}

 
@router.post("/initialize-reading-test", response_model=dict)
async def initialize_reading_test(
    test_data: ReadingTestInit,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Initialize a new IELTS reading test with three parts"""
    
    # Check if an exam with the same title already exists
    existing_exam = db.query(Exam).filter(Exam.title == test_data.title).first()
    if existing_exam:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An exam with the title '{test_data.title}' already exists"
        )
    
    # Create new exam
    new_exam = Exam(
        title=test_data.title,
        created_at=get_vietnam_time().replace(tzinfo=None),
        description=test_data.description,
        is_active=False,  # Will be set to true when all parts are completed
        created_by=current_admin.user_id
    )
    db.add(new_exam)
    db.flush()
    
    # Create 3 sections for IELTS reading parts with specific question counts
    part_question_counts = {1: 13, 2: 13, 3: 14}
    part_descriptions = [
        test_data.part1_description,
        test_data.part2_description,
        test_data.part3_description
    ]
    
    for part in range(1, 4):
        reading_section = ExamSection(
            exam_id=new_exam.exam_id,
            section_type='reading',
            duration=test_data.duration // 3,  # Split duration among parts
            total_marks=test_data.total_marks * (part_question_counts[part] / 40),  # Proportional marks
            order_number=part,
            description=part_descriptions[part - 1]
        )
        db.add(reading_section)
    
    db.commit()
    
    return {
        "message": "Reading test initialized successfully",
        "exam_id": new_exam.exam_id,
        "title": new_exam.title,
        "description": new_exam.description,
        "parts": 3,
        "question_counts": part_question_counts
    }

# Get all reading tests
@router.get("/reading-tests", response_model=List[dict])
async def get_reading_tests(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all reading tests"""
    
    # Query exams that have reading sections
    reading_exams = db.query(Exam)\
        .join(ExamSection)\
        .filter(ExamSection.section_type == 'reading')\
        .distinct()\
        .all()
    
    result = []
    for exam in reading_exams:
        # Get sections
        sections = db.query(ExamSection)\
            .filter(
                ExamSection.exam_id == exam.exam_id,
                ExamSection.section_type == 'reading'
            )\
            .order_by(ExamSection.order_number)\
            .all()
        
        # Check if each section has a passage
        section_status = []
        for section in sections:
            passage = db.query(ReadingPassage)\
                .filter(ReadingPassage.section_id == section.section_id)\
                .first()
            
            questions_count = db.query(func.count(Question.question_id))\
                .filter(Question.section_id == section.section_id)\
                .scalar()
            
            # Get expected question count based on part number
            expected_count = 13 if section.order_number < 3 else 14
            
            section_status.append({
                "section_id": section.section_id,
                "order_number": section.order_number,
                "has_passage": passage is not None,
                "questions_count": questions_count or 0,
                "expected_questions": expected_count,
                "is_complete": (passage is not None) and (questions_count == expected_count)
            })
        
        result.append({
            "exam_id": exam.exam_id,
            "title": exam.title,
            "created_at": exam.created_at,
            "is_active": exam.is_active,
            "sections": section_status
        })
    
    return result

# Get a specific reading test
@router.get("/reading-test/{exam_id}", response_model=dict)
async def get_reading_test(
    exam_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get details of a specific reading test"""
    
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
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
        passage = db.query(ReadingPassage)\
            .filter(ReadingPassage.section_id == section.section_id)\
            .first()
        
        # Get question groups
        question_groups = db.query(QuestionGroup)\
            .filter(QuestionGroup.section_id == section.section_id)\
            .order_by(QuestionGroup.order_number)\
            .all()
        
        group_details = []
        for group in question_groups:
            questions = db.query(Question)\
                .filter(Question.group_id == group.group_id)\
                .order_by(Question.question_number)\
                .all()
            
            question_details = []
            for question in questions:
                options = db.query(QuestionOption)\
                    .filter(QuestionOption.question_id == question.question_id)\
                    .all()
                
                question_details.append({
                    "question_id": question.question_id,
                    "question_number": question.question_number,
                    "text": question.question_text,
                    "type": question.question_type,
                    "marks": question.marks,
                    "correct_answer": question.correct_answer,
                    "explanation": question.explanation,
                    "locate": question.locate,
                    "options": [
                        {
                            "option_id": option.option_id,
                            "text": option.option_text,
                            "is_correct": option.is_correct
                        } for option in options
                    ] if options else []
                })
            
            group_details.append({
                "group_id": group.group_id,
                "instruction": group.instruction,
                "question_range": group.question_range,
                "group_type": group.group_type,
                "order_number": group.order_number,
                "questions": question_details
            })
        
        # Expected question count based on part number
        expected_count = 13 if section.order_number < 3 else 14
        
        section_details.append({
            "section_id": section.section_id,
            "order_number": section.order_number,
            "duration": section.duration,
            "total_marks": section.total_marks,
            "description": section.description,
            "is_forecast": getattr(section, 'is_forecast', False),
            "forecast_title": getattr(section, 'forecast_title', None),
            "is_recommended": getattr(section, 'is_recommended', False),
            "expected_questions": expected_count,
            "passage": {
                "passage_id": passage.passage_id if passage else None,
                "title": passage.title if passage else None,
                "content": passage.content if passage else None,
                "word_count": passage.word_count if passage else 0
            } if passage else None,
            "question_groups": group_details
        })
    
    return {
        "exam_id": exam.exam_id,
        "title": exam.title,
        "created_at": exam.created_at,
        "is_active": exam.is_active,
        "sections": section_details
    }

@router.put("/reading-test/{exam_id}/forecast", response_model=dict)
async def update_reading_forecast(
    exam_id: int,
    update: ReadingForecastUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.section_type == 'reading',
        ExamSection.order_number == update.part_number
    ).first()
    if not section:
        raise HTTPException(status_code=404, detail="Reading section not found")
    section.is_forecast = update.is_forecast
    section.forecast_title = update.forecast_title if update.is_forecast else None
    if update.is_recommended is not None:
        section.is_recommended = update.is_recommended
    db.add(section)
    db.commit()
    db.refresh(section)
    return {
        "message": "Reading forecast updated",
        "exam_id": exam_id,
        "part_number": update.part_number,
        "is_forecast": section.is_forecast,
        "forecast_title": section.forecast_title,
        "is_recommended": getattr(section, 'is_recommended', False)
    }

class ReadingTestDescriptionsUpdate(BaseModel):
    description: Optional[str] = None  # Main exam description
    part1_description: Optional[str] = None
    part2_description: Optional[str] = None
    part3_description: Optional[str] = None

@router.get("/reading-test/{exam_id}/descriptions", response_model=dict)
async def get_reading_test_descriptions(
    exam_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get the descriptions/part titles of a reading test"""
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Reading test not found")
    
    sections = db.query(ExamSection)\
        .filter(ExamSection.exam_id == exam_id, ExamSection.section_type == 'reading')\
        .order_by(ExamSection.order_number).all()
    
    descriptions = {
        "exam_id": exam_id,
        "title": exam.title,
        "description": exam.description,
        "part1_description": None,
        "part2_description": None,
        "part3_description": None,
        "parts_count": len(sections)
    }
    for section in sections:
        if 1 <= section.order_number <= 3:
            descriptions[f"part{section.order_number}_description"] = section.part_title
    return descriptions

@router.put("/reading-test/{exam_id}/descriptions", response_model=dict)
async def update_reading_test_descriptions(
    exam_id: int,
    descriptions_data: ReadingTestDescriptionsUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update the descriptions/part titles of a reading test"""
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Reading test not found")
    
    sections = db.query(ExamSection)\
        .filter(ExamSection.exam_id == exam_id, ExamSection.section_type == 'reading')\
        .order_by(ExamSection.order_number).all()
    
    if descriptions_data.description is not None:
        exam.description = descriptions_data.description
        db.add(exam)
    
    part_descriptions = [
        descriptions_data.part1_description,
        descriptions_data.part2_description,
        descriptions_data.part3_description
    ]
    for section in sections:
        idx = section.order_number - 1
        if 0 <= idx < 3 and part_descriptions[idx] is not None:
            section.part_title = part_descriptions[idx]
            db.add(section)
    
    db.commit()
    return {
        "message": "Reading test descriptions updated successfully",
        "exam_id": exam_id,
        "title": exam.title
    }

# Edit reading part
@router.put("/reading-test/{exam_id}/part/{part_number}", response_model=dict)
async def update_reading_part(
    exam_id: int,
    part_number: int,
    passage_content: str = Form(...),
    question_content: str = Form(...),
    passage_title: str = Form(...),
    questions_json: str = Form(...),
    description: Optional[str] = Form(None),
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update a reading part with passage and question groups"""
    
    if not 1 <= part_number <= 3:
        raise HTTPException(status_code=400, detail="Part number must be between 1 and 3")
    
    try:
        questions_data = json.loads(questions_json)
        if not isinstance(questions_data, list):
            raise HTTPException(status_code=400, detail="Questions data must be an array")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid questions JSON format")
    
    # Get the exam
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Reading test not found")
    
    # Get the section
    section = db.query(ExamSection)\
        .filter(
            ExamSection.exam_id == exam_id,
            ExamSection.section_type == 'reading',
            ExamSection.order_number == part_number
        )\
        .first()
    
    if not section:
        raise HTTPException(status_code=404, detail="Reading section not found")
    
    # Update section description if provided
    if description:
        section.description = description
    
    # Validate question count based on part number
    expected_count = 13 if part_number < 3 else 14
    if len(questions_data) != expected_count:
        raise HTTPException(
            status_code=400, 
            detail=f"Part {part_number} must have exactly {expected_count} questions, but got {len(questions_data)}"
        )
    
    # Delete existing passage if it exists
    db.query(ReadingPassage).filter(ReadingPassage.section_id == section.section_id).delete()
    
    # Get existing questions to delete their related records
    existing_questions = db.query(Question).filter(Question.section_id == section.section_id).all()
    
    # First delete related StudentAnswer records to avoid foreign key constraint violation
    for q in existing_questions:
        # Delete related StudentAnswer records
        db.query(StudentAnswer).filter(StudentAnswer.question_id == q.question_id).delete()
        # Delete related QuestionOption records
        db.query(QuestionOption).filter(QuestionOption.question_id == q.question_id).delete()
    
    # Now it's safe to delete the questions
    db.query(Question).filter(Question.section_id == section.section_id).delete()
    
    # Delete existing question groups
    db.query(QuestionGroup).filter(QuestionGroup.section_id == section.section_id).delete()
    
    # Create new passage
    word_count = len(passage_content.split())
    new_passage = ReadingPassage(
        section_id=section.section_id,
        content=passage_content,
        title=passage_title,
        word_count=word_count
    )
    db.add(new_passage)
    db.flush()

     # Create main text question for the passage
    main_question = Question(
        section_id=section.section_id,
        question_type='main_text',
        question_text=question_content,
        additional_data={'part_number': part_number, 'title': passage_title}
    )
    db.add(main_question)
    db.flush()
    # Rest of the function remains the same...
    # Parse both passage content and question content to extract question contexts
    from bs4 import BeautifulSoup
    
    question_contexts = []
    processed_numbers = set()
    
    # Function to extract only the sentence containing the question number
    import re
    
    def extract_sentence_containing_number(full_text, number_str):
        """Extract just the sentence containing the question number from the full text."""
        # Try to find the sentence containing this number
        # Look for the number with word boundaries
        pattern = rf'\b{re.escape(number_str)}\b'
        match = re.search(pattern, full_text)
        
        if not match:
            return f"Question {number_str}"
        
        pos = match.start()
        
        # Find sentence start (look for period, question mark, exclamation, or start of text)
        sentence_start = 0
        for i in range(pos - 1, -1, -1):
            if full_text[i] in '.!?':
                sentence_start = i + 1
                break
        
        # Find sentence end (look for period, question mark, exclamation, or end of text)
        sentence_end = len(full_text)
        for i in range(pos + len(number_str), len(full_text)):
            if full_text[i] in '.!?':
                sentence_end = i + 1
                break
        
        sentence = full_text[sentence_start:sentence_end].strip()
        
        # If the sentence is too short or empty, return a more meaningful context
        if len(sentence) < 10:
            return f"Question {number_str}"
        
        return sentence
    
    # Function to parse content for question contexts
    def parse_content_for_contexts(content):
        soup = BeautifulSoup(content, 'html.parser')
        bold_elements = soup.find_all(['strong', 'b'])
        
        for bold_elem in bold_elements:
            text = bold_elem.get_text().strip()
            parent = bold_elem.find_parent(['p', 'div', 'td'])
            if parent:
                full_text = parent.get_text().strip()
                
                if text.isdigit():
                    num = int(text)
                    if num not in processed_numbers:
                        processed_numbers.add(num)
                        # Extract only the sentence containing this question number
                        context = extract_sentence_containing_number(full_text, text)
                        question_contexts.append({
                            'number': num,
                            'context': context,
                            'required_choices': None,
                        })
                elif "Choose THREE" in text or "THREE" in text:
                    context = extract_sentence_containing_number(full_text, "THREE")
                    for i in range(3):
                        question_contexts.append({
                            'number': None,
                            'context': context,
                            'required_choices': 3,
                        })
                elif "Choose TWO" in text or "TWO" in text:
                    context = extract_sentence_containing_number(full_text, "TWO")
                    for i in range(2):
                        question_contexts.append({
                            'number': None,
                            'context': context,
                            'required_choices': 2,
                        })
    
    # Parse both contents
    parse_content_for_contexts(passage_content)
    parse_content_for_contexts(question_content)
    
    # Group questions by type and maintain original question numbers
    question_groups = {}
    question_number_map = {}
    
    # First, create a mapping of frontend indices to actual question numbers
    for i, q_data in enumerate(questions_data):
        q_type = q_data['question_type']
        if q_type not in question_groups:
            question_groups[q_type] = []
        
        # The frontend already calculates the correct question number and sends it in question_text
        # Format: "Question 25" -> extract 25
        actual_number = None
        question_text = q_data.get('question_text', '')
        
        # Try to extract question number from the frontend's question_text (e.g., "Question 25")
        if question_text.startswith('Question '):
            try:
                extracted_num = int(question_text.replace('Question ', '').strip())
                actual_number = extracted_num
            except (ValueError, AttributeError):
                pass
        
        # If extraction from frontend failed, try to find matching context
        if actual_number is None:
            for ctx in question_contexts:
                if ctx['number'] == i + 1:  # Try to match by index
                    actual_number = ctx['number']
                    break
        
        if actual_number is None:
            # If no match by index, try to find by scanning all contexts
            for ctx in question_contexts:
                if ctx['number'] and ctx['number'] not in question_number_map.values():
                    actual_number = ctx['number']
                    break
        
        if actual_number is None:
            # If still no match, use the frontend index + 1 (may be incorrect for Part 2/3)
            actual_number = i + 1
        
        question_number_map[i] = actual_number
        question_groups[q_type].append((i, q_data, actual_number))
    
    # Create question groups and questions
    total_marks = 0
    group_order = 1
    
    for q_type, questions in question_groups.items():
        # Determine question range using actual numbers
        start_num = min([actual_num for _, _, actual_num in questions])
        end_num = max([actual_num for _, _, actual_num in questions])
        question_range = f"{start_num}-{end_num}"
        
        # Create question group
        instruction = f"Questions {question_range}"
        new_group = QuestionGroup(
            section_id=section.section_id,
            instruction=instruction,
            question_range=question_range,
            group_type=q_type,
            order_number=group_order
        )
        db.add(new_group)
        db.flush()
        group_order += 1
        
        # Create questions for this group
        for i, q_data, actual_number in questions:
            # Find matching context for this question
            context_data = None
            for ctx in question_contexts:
                if ctx['number'] == actual_number:
                    context_data = ctx
                    break
            
            # If no specific context found, use the question text from frontend
            question_text = q_data['question_text']
            required_choices = None
            
            if context_data:
                question_text = context_data['context']
                required_choices = context_data.get('required_choices')
            
            question = Question(
                section_id=section.section_id,
                group_id=new_group.group_id,
                question_type=q_data['question_type'],
                question_text=question_text,
                correct_answer=q_data['correct_answer'],
                marks=int(q_data['marks']),
                question_number=actual_number,  # Use the actual question number
                explanation=q_data.get('explanation'),
                locate=q_data.get('locate'),
                additional_data={
                    'main_text_id': main_question.question_id,
                    'required_choices': required_choices or q_data.get('required_choices')
                }
            )
            db.add(question)
            db.flush()
            
            # Handle options if present
            if q_data.get('options'):
                if not isinstance(q_data['options'], list):
                    raise HTTPException(status_code=400, detail="Options must be an array")
                    
                for opt_data in q_data['options']:
                    if not all(field in opt_data for field in ['option_text', 'is_correct']):
                        raise HTTPException(status_code=400, detail="Missing required fields in option data")
                        
                    option = QuestionOption(
                        question_id=question.question_id,
                        option_text=opt_data['option_text'].strip(),
                        is_correct=bool(opt_data['is_correct'])
                    )
                    db.add(option)
            
            total_marks += int(q_data['marks'])
    
    # Update section marks
    section.total_marks = total_marks
    db.add(section)
    
    # Check if all parts are completed
    all_parts_completed = True
    all_sections = db.query(ExamSection)\
        .filter(
            ExamSection.exam_id == exam_id,
            ExamSection.section_type == 'reading'
        )\
        .all()
    
    for s in all_sections:
        passage_exists = db.query(ReadingPassage)\
            .filter(ReadingPassage.section_id == s.section_id)\
            .first() is not None
        
        # Check if the section has the correct number of questions
        expected_q_count = 13 if s.order_number < 3 else 14
        actual_q_count = db.query(func.count(Question.question_id))\
            .filter(Question.section_id == s.section_id, Question.question_type != 'main_text')\
            .scalar() or 0
        
        if not (passage_exists and actual_q_count == expected_q_count):
            all_parts_completed = False
            break
    
    # If all parts are completed, activate the exam
    if all_parts_completed:
        exam.is_active = True
        db.add(exam)
    
    db.commit()
    
    return {
        "message": f"Reading part {part_number} updated successfully",
        "exam_id": exam_id,
        "section_id": section.section_id,
        "passage_id": new_passage.passage_id,
        "passage_title": passage_title,
        "word_count": word_count,
        "total_questions": len(questions_data),
        "expected_questions": expected_count,
        "total_marks": total_marks,
        "is_exam_active": all_parts_completed,
        "questions_found": len(question_contexts)
    }

# Get a specific reading test part
@router.get("/reading-test/{exam_id}/part-details/{part_number}", response_model=dict)
async def get_reading_test_part(
    exam_id: int,
    part_number: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get details of a specific reading test part"""
    
    if not 1 <= part_number <= 3:
        raise HTTPException(status_code=400, detail="Part number must be between 1 and 3")
    
    # Get the exam
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Reading test not found")
    
    # Get the section
    section = db.query(ExamSection)\
        .filter(
            ExamSection.exam_id == exam_id,
            ExamSection.section_type == 'reading',
            ExamSection.order_number == part_number
        )\
        .first()
    
    if not section:
        raise HTTPException(status_code=404, detail="Reading section not found")
    
    # Get passage
    passage = db.query(ReadingPassage)\
        .filter(ReadingPassage.section_id == section.section_id)\
        .first()
    
    # Get question groups
    question_groups = db.query(QuestionGroup)\
        .filter(QuestionGroup.section_id == section.section_id)\
        .order_by(QuestionGroup.order_number)\
        .all()
    
    # Get main text question
    main_text_question = db.query(Question)\
        .filter(
            Question.section_id == section.section_id,
            Question.question_type == 'main_text'
        )\
        .first()
    
    group_details = []
    for group in question_groups:
        questions = db.query(Question)\
            .filter(Question.group_id == group.group_id)\
            .order_by(Question.question_number)\
            .all()
        
        question_details = []
        for question in questions:
            options = db.query(QuestionOption)\
                .filter(QuestionOption.question_id == question.question_id)\
                .all()
            
            question_details.append({
                "question_id": question.question_id,
                "question_number": question.question_number,
                "text": question.question_text,
                "type": question.question_type,
                "marks": question.marks,
                "correct_answer": question.correct_answer,
                "explanation": question.explanation,
                "locate": question.locate,
                "options": [
                    {
                        "option_id": option.option_id,
                        "text": option.option_text,
                        "is_correct": option.is_correct
                    } for option in options
                ] if options else []
            })
        
        group_details.append({
            "group_id": group.group_id,
            "instruction": group.instruction,
            "question_range": group.question_range,
            "group_type": group.group_type,
            "order_number": group.order_number,
            "questions": question_details
        })
    
    return {
        "exam_id": exam.exam_id,
        "part_number": part_number,
        "section_id": section.section_id,
        "description": section.description,
        "passage": {
            "passage_id": passage.passage_id if passage else None,
            "title": passage.title if passage else "",
            "content": passage.content if passage else "",
            "word_count": passage.word_count if passage else 0
        } if passage else None,
        "question_groups": group_details,
        "questions": main_text_question.question_text if main_text_question else ""
    }

# Delete a reading test
@router.delete("/reading-test/{exam_id}", response_model=dict)
async def delete_reading_test(
    exam_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Delete a reading test and all its components"""
    
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Reading test not found")
    
    # Get all sections
    sections = db.query(ExamSection)\
        .filter(
            ExamSection.exam_id == exam_id,
            ExamSection.section_type == 'reading'
        )\
        .all()
    
    for section in sections:
        # Delete passages
        db.query(ReadingPassage)\
            .filter(ReadingPassage.section_id == section.section_id)\
            .delete()
        
        # Delete question groups and questions
        groups = db.query(QuestionGroup)\
            .filter(QuestionGroup.section_id == section.section_id)\
            .all()
        
        for group in groups:
            questions = db.query(Question)\
                .filter(Question.group_id == group.group_id)\
                .all()
            
            for question in questions:
                db.query(QuestionOption)\
                    .filter(QuestionOption.question_id == question.question_id)\
                    .delete()
                
                db.delete(question)
            
            db.delete(group)
        
        # Delete section
        db.delete(section)
    
    # Delete exam
    db.delete(exam)
    db.commit()
    
    return {
        "message": "Reading test deleted successfully",
        "exam_id": exam_id
    }
