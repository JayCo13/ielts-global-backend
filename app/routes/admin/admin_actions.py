import json
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Form
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Exam, ExamSection, Question, QuestionOption, ReadingPassage, WritingAnswer, QuestionGroup, ListeningMedia, WritingTask, User, ExamResult, PackageTransaction, StudentAnswer, VIPPackage, VIPSubscription, ExamAccessType, AdminNotificationRead, SpeakingMaterial, SpeakingMaterialAccessType
from app.routes.admin.auth import get_current_admin
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from pydantic import BaseModel
import shutil
from bs4 import BeautifulSoup
import re
import os
from uuid import uuid4
from sqlalchemy.sql import func
from sqlalchemy import and_, distinct, or_
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()
class ExamDescriptionUpdate(BaseModel):
    description: str

class QuestionOptionCreate(BaseModel):
    option_text: str
    is_correct: bool

class ExamAccessUpdate(BaseModel):
    access_types: List[str] 

class ListeningTestTitleUpdate(BaseModel):
    title: str

class NotificationMarkRead(BaseModel):
    notification_ids: List[str]

class QuestionCreate(BaseModel):
    question_text: str
    question_type: str
    correct_answer: str
    marks: int
    media_url: Optional[str] = None
    options: List[QuestionOptionCreate] = None
    required_choices: Optional[int] = None
    
class ListeningTestInit(BaseModel):
    title: str
    description: Optional[str] = None
    duration: int = 30
    total_marks: float = 40.0
    part1_description: Optional[str] = None
    part2_description: Optional[str] = None
    part3_description: Optional[str] = None
    part4_description: Optional[str] = None

class ListeningTestDescriptionsUpdate(BaseModel):
    description: Optional[str] = None
    part1_description: Optional[str] = None
    part2_description: Optional[str] = None
    part3_description: Optional[str] = None
    part4_description: Optional[str] = None

class ListeningForecastUpdate(BaseModel):
    part_number: int
    is_forecast: bool
    forecast_title: Optional[str] = None
    is_recommended: Optional[bool] = None


class ExamSectionCreate(BaseModel):
    section_type: str
    duration: int
    total_marks: float
    order_number: int
    questions: List[QuestionCreate]

class IELTSExamCreate(BaseModel):
    title: str
    sections: List[ExamSectionCreate]

SPEAKING_PDF_DIR = "static/speaking_pdfs"
VALID_SPEAKING_PARTS = {"part1", "part2_3"}

@router.post("/speaking/materials", response_model=dict)
async def create_speaking_material(
    title: str = Form(...),
    part_type: str = Form(...),
    pdf_file: UploadFile = File(...),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    if part_type not in VALID_SPEAKING_PARTS:
        raise HTTPException(status_code=400, detail="Invalid part_type. Use 'part1' or 'part2_3'")
    if pdf_file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    os.makedirs(SPEAKING_PDF_DIR, exist_ok=True)
    unique_filename = f"{uuid4()}.pdf"
    file_path = os.path.join(SPEAKING_PDF_DIR, unique_filename)
    with open(file_path, "wb") as buffer:
        content = await pdf_file.read()
        buffer.write(content)

    pdf_url = f"/static/speaking_pdfs/{unique_filename}"

    material = SpeakingMaterial(
        title=title,
        part_type=part_type,
        pdf_url=pdf_url,
        created_at=get_vietnam_time().replace(tzinfo=None)
    )
    db.add(material)
    db.commit()
    db.refresh(material)

    return {
        "material_id": material.material_id,
        "title": material.title,
        "part_type": material.part_type,
        "pdf_url": material.pdf_url,
        "created_at": material.created_at
    }

@router.get("/speaking/materials", response_model=List[dict])
async def list_speaking_materials(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    materials = db.query(SpeakingMaterial).order_by(SpeakingMaterial.created_at.desc()).all()
    return [{
        "material_id": m.material_id,
        "title": m.title,
        "part_type": m.part_type,
        "pdf_url": m.pdf_url,
        "created_at": m.created_at
    } for m in materials]

@router.get("/speaking/materials/{material_id}", response_model=dict)
async def get_speaking_material(
    material_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    m = db.query(SpeakingMaterial).filter(SpeakingMaterial.material_id == material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Material not found")
    return {
        "material_id": m.material_id,
        "title": m.title,
        "part_type": m.part_type,
        "pdf_url": m.pdf_url,
        "created_at": m.created_at
    }

@router.put("/speaking/materials/{material_id}", response_model=dict)
async def update_speaking_material(
    material_id: int,
    title: Optional[str] = Form(None),
    part_type: Optional[str] = Form(None),
    pdf_file: Optional[UploadFile] = File(None),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    m = db.query(SpeakingMaterial).filter(SpeakingMaterial.material_id == material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Material not found")

    if part_type:
        if part_type not in VALID_SPEAKING_PARTS:
            raise HTTPException(status_code=400, detail="Invalid part_type. Use 'part1' or 'part2_3'")
        m.part_type = part_type
    if title:
        m.title = title

    if pdf_file:
        if pdf_file.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Only PDF files are allowed")
        os.makedirs(SPEAKING_PDF_DIR, exist_ok=True)
        unique_filename = f"{uuid4()}.pdf"
        file_path = os.path.join(SPEAKING_PDF_DIR, unique_filename)
        with open(file_path, "wb") as buffer:
            content = await pdf_file.read()
            buffer.write(content)
        # delete old file if exists
        try:
            old_path = m.pdf_url.lstrip('/') if m.pdf_url else None
            if old_path and os.path.exists(old_path):
                os.remove(old_path)
        except Exception:
            pass
        m.pdf_url = f"/static/speaking_pdfs/{unique_filename}"

    db.add(m)
    db.commit()
    db.refresh(m)

    return {
        "material_id": m.material_id,
        "title": m.title,
        "part_type": m.part_type,
        "pdf_url": m.pdf_url,
        "created_at": m.created_at
    }

@router.delete("/speaking/materials/{material_id}", response_model=dict)
async def delete_speaking_material(
    material_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    m = db.query(SpeakingMaterial).filter(SpeakingMaterial.material_id == material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Material not found")
    # delete file
    try:
        old_path = m.pdf_url.lstrip('/') if m.pdf_url else None
        if old_path and os.path.exists(old_path):
            os.remove(old_path)
    except Exception:
        pass
    db.delete(m)
    db.commit()
    return {"message": "Deleted"}


class SpeakingMaterialAccessUpdate(BaseModel):
    access_types: List[str]


@router.put("/speaking/materials/{material_id}/access", response_model=dict)
async def update_speaking_material_access(
    material_id: int,
    access_data: SpeakingMaterialAccessUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update speaking material access types"""
    
    # Validate access types
    valid_types = ['no vip', 'vip', 'student']
    for access_type in access_data.access_types:
        if access_type not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid access type: {access_type}. Must be one of: {', '.join(valid_types)}"
            )
    
    # Check if material exists
    material = db.query(SpeakingMaterial).filter(SpeakingMaterial.material_id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="Speaking material not found")
    
    # Delete existing access types
    db.query(SpeakingMaterialAccessType).filter(SpeakingMaterialAccessType.material_id == material_id).delete()
    
    # Add new access types
    for access_type in access_data.access_types:
        new_access = SpeakingMaterialAccessType(
            material_id=material_id,
            access_type=access_type
        )
        db.add(new_access)
    
    db.commit()
    
    return {
        "message": "Speaking material access types updated successfully",
        "material_id": material_id,
        "access_types": access_data.access_types
    }


@router.get("/speaking/materials/{material_id}/access", response_model=dict)
async def get_speaking_material_access(
    material_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get current access types for a speaking material"""
    
    # Check if material exists
    material = db.query(SpeakingMaterial).filter(SpeakingMaterial.material_id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="Speaking material not found")
    
    # Get access types
    access_types = db.query(SpeakingMaterialAccessType)\
        .filter(SpeakingMaterialAccessType.material_id == material_id)\
        .all()
    
    return {
        "material_id": material_id,
        "title": material.title,
        "access_types": [access.access_type for access in access_types]
    }

class ReadingQuestionCreate(BaseModel):
    question_text: str
    question_type: str
    correct_answer: str
    marks: int
    passage_reference: Optional[str] = None
    options: List[QuestionOptionCreate] = None

class ReadingTestCreate(BaseModel):
    title: str
    passages: List[str]
    questions: List[ReadingQuestionCreate]
    duration: int = 60
    total_marks: float = 40.0

class ListeningQuestionCreate(BaseModel):
    title: str
    audio_url: str
    transcript: Optional[str] = None
    questions: List[QuestionCreate]
    duration: int = 30
    total_marks: float = 40.0

# Add these new models at the top with other BaseModel classes

# Add these new models at the top with other BaseModel classes
class WritingTestInit(BaseModel):
    title: str

class WritingTaskCreate(BaseModel):
    part_number: int  # 1 or 2
    task_type: str  # 'essay', 'report', or 'letter'
    title: str | None = None
    instructions: str
    word_limit: int
    total_marks: float = 20.0  # default marks for each part
    duration: int = 60  # default duration in minutes
    is_forecast: bool = False
    sample_essay: str | None = None
@router.put("/ielts-exams/{exam_id}/access", response_model=dict)
async def update_exam_access(
    exam_id: int,
    access_data: ExamAccessUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update exam access types"""
    
    # Validate access types
    valid_types = ['no vip', 'vip', 'student']
    for access_type in access_data.access_types:
        if access_type not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid access type: {access_type}. Must be one of: {', '.join(valid_types)}"
            )
    
    # Check if exam exists
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Delete existing access types
    db.query(ExamAccessType).filter(ExamAccessType.exam_id == exam_id).delete()
    
    # Add new access types
    for access_type in access_data.access_types:
        new_access = ExamAccessType(
            exam_id=exam_id,
            access_type=access_type
        )
        db.add(new_access)
    
    db.commit()
    
    return {
        "message": "Exam access types updated successfully",
        "exam_id": exam_id,
        "access_types": access_data.access_types
    }
@router.get("/ielts-exams/{exam_id}/access", response_model=dict)
async def get_exam_access_types(
    exam_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get current access types for an exam"""
    
    # Check if exam exists
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Get access types
    access_types = db.query(ExamAccessType)\
        .filter(ExamAccessType.exam_id == exam_id)\
        .all()
    
    return {
        "exam_id": exam_id,
        "title": exam.title,
        "access_types": [access.access_type for access in access_types]
    }

@router.post("/initialize-listening-test", response_model=dict)
async def initialize_listening_test(
    test_data: ListeningTestInit,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    # Check if an exam with the same title already exists
    existing_exam = db.query(Exam).filter(Exam.title == test_data.title).first()
    if existing_exam:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An exam with the title '{test_data.title}' already exists"
        )
    
    new_exam = Exam(
        title=test_data.title,
        description=test_data.description,
        created_at=get_vietnam_time().replace(tzinfo=None),
        is_active=True,
        created_by=current_admin.user_id
    )
    db.add(new_exam)
    db.flush()

    # Create 4 sections for IELTS listening parts
    part_descriptions = [
        test_data.part1_description,
        test_data.part2_description,
        test_data.part3_description,
        test_data.part4_description
    ]
    
    for part in range(1, 5):
        listening_section = ExamSection(
            exam_id=new_exam.exam_id,
            section_type='listening',
            duration=test_data.duration // 4,  # Split duration among parts
            total_marks=test_data.total_marks / 4,  # Split marks among parts
            order_number=part,
            description=part_descriptions[part - 1]
        )
        db.add(listening_section)
    
    db.commit()
    return {
        "message": "Listening test initialized successfully",
        "exam_id": new_exam.exam_id,
        "description": new_exam.description,
        "title": new_exam.title
    }
# ... existing code ...
@router.put("/listening-test/{exam_id}/title", response_model=dict)
async def update_listening_test_title(
    exam_id: int,
    title_data: ListeningTestTitleUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update the title of a listening test"""
    
    # Check if exam exists
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Listening test not found")
    
    # Check if new title already exists (excluding current exam)
    existing_exam = db.query(Exam).filter(
        Exam.title == title_data.title,
        Exam.exam_id != exam_id
    ).first()
    
    if existing_exam:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An exam with the title '{title_data.title}' already exists"
        )
    
    # Update the title
    exam.title = title_data.title
    db.commit()
    
    return {
        "message": "Listening test title updated successfully",
        "exam_id": exam_id,
        "title": exam.title
    }

@router.get("/listening-test/{exam_id}/descriptions", response_model=dict)
async def get_listening_test_descriptions(
    exam_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get the descriptions of a listening test (main description and all four part descriptions)"""
    
    # Check if exam exists
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Listening test not found")
    
    # Get all sections for this exam
    sections = db.query(ExamSection)\
        .filter(
            ExamSection.exam_id == exam_id,
            ExamSection.section_type == 'listening'
        )\
        .order_by(ExamSection.order_number)\
        .all()
    
    if not sections or len(sections) != 4:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This exam does not have the expected 4 listening sections"
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
        "parts_count": len(sections)
    }
    
    # Get descriptions from each section based on order_number
    for section in sections:
        if 1 <= section.order_number <= 4:
            descriptions[f"part{section.order_number}_description"] = section.description
    
    return descriptions

@router.put("/listening-test/{exam_id}/descriptions", response_model=dict)
async def update_listening_test_descriptions(
    exam_id: int,
    descriptions_data: ListeningTestDescriptionsUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update the descriptions of a listening test (main description and all four part descriptions)"""
    
    # Check if exam exists
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Listening test not found")
    
    # Get all sections for this exam
    sections = db.query(ExamSection)\
        .filter(
            ExamSection.exam_id == exam_id,
            ExamSection.section_type == 'listening'
        )\
        .order_by(ExamSection.order_number)\
        .all()
    
    if not sections or len(sections) != 4:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This exam does not have the expected 4 listening sections"
        )
    
    # Update main exam description if provided
    if descriptions_data.description is not None:
        exam.description = descriptions_data.description
        db.add(exam)
    
    # Update part descriptions if provided
    part_descriptions = [
        descriptions_data.part1_description,
        descriptions_data.part2_description,
        descriptions_data.part3_description,
        descriptions_data.part4_description
    ]
    
    for i, section in enumerate(sections):
        if part_descriptions[i] is not None:
            section.description = part_descriptions[i]
            db.add(section)
    
    db.commit()
    
    return {
        "message": "Listening test descriptions updated successfully",
        "exam_id": exam_id,
        "title": exam.title
    }

class PartTitleUpdate(BaseModel):
    part_title: str

@router.put("/listening-test/{exam_id}/part/{part_number}/title", response_model=dict)
async def update_part_title(
    exam_id: int,
    part_number: int,
    data: PartTitleUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update the display title of a specific listening test part"""
    if not 1 <= part_number <= 4:
        raise HTTPException(status_code=400, detail="Part number must be between 1 and 4")
    
    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.order_number == part_number,
        ExamSection.section_type == 'listening'
    ).first()
    
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    
    # Strip HTML tags from title — only plain text allowed
    clean_title = data.part_title.strip()
    if clean_title.startswith('<'):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(clean_title, 'html.parser')
        clean_title = soup.get_text().strip()
    
    section.part_title = clean_title
    db.add(section)
    db.commit()
    
    return {
        "message": f"Part {part_number} title updated successfully",
        "part_title": clean_title
    }

@router.get("/listening-test/{exam_id}/part-titles", response_model=dict)
async def get_listening_part_titles(
    exam_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all part titles for a listening test (plain text only)"""
    sections = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.section_type == 'listening'
    ).order_by(ExamSection.order_number).all()
    
    titles = {}
    for section in sections:
        if 1 <= section.order_number <= 4:
            raw = section.part_title or ''
            # Strip HTML if present
            if raw.startswith('<'):
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(raw, 'html.parser')
                raw = soup.get_text().strip()
            titles[f"part{section.order_number}_title"] = raw
    
    return {"exam_id": exam_id, "parts_count": len(sections), **titles}

class ListeningPartTitlesUpdate(BaseModel):
    part1_title: Optional[str] = None
    part2_title: Optional[str] = None
    part3_title: Optional[str] = None
    part4_title: Optional[str] = None

@router.put("/listening-test/{exam_id}/part-titles", response_model=dict)
async def update_listening_part_titles(
    exam_id: int,
    data: ListeningPartTitlesUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Bulk update part titles for a listening test (plain text only)"""
    sections = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.section_type == 'listening'
    ).order_by(ExamSection.order_number).all()
    
    if not sections:
        raise HTTPException(status_code=404, detail="No listening sections found for this exam")
    
    titles = [data.part1_title, data.part2_title, data.part3_title, data.part4_title]
    
    for section in sections:
        idx = section.order_number - 1
        if 0 <= idx < 4 and titles[idx] is not None:
            clean = titles[idx].strip()
            # Strip HTML if present
            if clean.startswith('<'):
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(clean, 'html.parser')
                clean = soup.get_text().strip()
            section.part_title = clean
            db.add(section)
    
    db.commit()
    
    return {"message": "Part titles updated successfully", "exam_id": exam_id}
    
@router.get("/listening-test/{exam_id}", response_model=dict)
async def get_listening_test_details(
    exam_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get details of a specific listening test for editing"""
    
    # Check if exam exists and is a listening test
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Listening test not found")
    
    # Get all sections for this exam
    sections = db.query(ExamSection)\
        .filter(
            ExamSection.exam_id == exam_id,
            ExamSection.section_type == 'listening'
        )\
        .order_by(ExamSection.order_number)\
        .all()
    
    if not sections:
        raise HTTPException(status_code=404, detail="No listening sections found for this test")
    
    # Prepare response with test metadata
    response = {
        "exam_id": exam.exam_id,
        "title": exam.title,
        "description": exam.description,
        "created_at": exam.created_at,
        "is_active": exam.is_active,
        "parts": []
    }
    
    # Get details for each part
    for section in sections:
        # Get listening media for this section
        media = db.query(ListeningMedia)\
            .filter(ListeningMedia.section_id == section.section_id)\
            .first()
        
        # Get questions for this section
        questions = db.query(Question)\
            .filter(Question.section_id == section.section_id)\
            .order_by(Question.question_id)\
            .all()
        
        # Skip the main text question which just holds the transcript
        questions = [q for q in questions if q.question_type != 'main_text']
        
        # Format questions with their options
        formatted_questions = []
        for question in questions:
            # Get options for this question
            options = db.query(QuestionOption)\
                .filter(QuestionOption.question_id == question.question_id)\
                .all()
            
            # Format question data
            question_data = {
                "question_id": question.question_id,
                "question_type": question.question_type,
                "question_text": question.question_text,
                "correct_answer": question.correct_answer,
                "marks": question.marks,
                "options": []
            }
            
            # Add options if they exist
            if options:
                question_data["options"] = [
                    {
                        "option_id": opt.option_id,
                        "option_text": opt.option_text,
                        "is_correct": opt.is_correct
                    } for opt in options
                ]
            
            formatted_questions.append(question_data)
        
        # Add part data to response
        part_data = {
            "section_id": section.section_id,
            "part_number": section.order_number,
            "duration": section.duration,
            "total_marks": section.total_marks,
            "description": section.description,
            "is_forecast": bool(getattr(section, 'is_forecast', False)),
            "forecast_title": getattr(section, 'forecast_title', None),
            "is_recommended": bool(getattr(section, 'is_recommended', False)),
            "questions": formatted_questions,
        }
        
        # Add media data if it exists
        if media:
            part_data["media"] = {
                "media_id": media.media_id,
                "transcript": media.transcript,
                "audio_filename": media.audio_filename
            }
        
        response["parts"].append(part_data)
    
    return response

@router.put("/listening-test/{exam_id}/forecast", response_model=dict)
async def update_listening_forecast(
    exam_id: int,
    update: ListeningForecastUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    if not 1 <= update.part_number <= 4:
        raise HTTPException(status_code=400, detail="Part number must be between 1 and 4")
    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.order_number == update.part_number,
        ExamSection.section_type == 'listening'
    ).first()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    section.is_forecast = update.is_forecast
    section.forecast_title = update.forecast_title if update.is_forecast else None
    if update.is_recommended is not None:
        section.is_recommended = update.is_recommended
    db.add(section)
    db.commit()
    return {
        "exam_id": exam_id,
        "part_number": update.part_number,
        "is_forecast": section.is_forecast,
        "forecast_title": section.forecast_title,
        "is_recommended": bool(getattr(section, 'is_recommended', False))
    }

@router.get("/listening-test/{exam_id}/part/{part_number}", response_model=dict)
async def get_listening_part(
    exam_id: int,
    part_number: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get details of a specific part of a listening test for editing"""
    
    if not 1 <= part_number <= 4:
        raise HTTPException(status_code=400, detail="Part number must be between 1 and 4")
    
    # Get the section for this part
    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.order_number == part_number,
        ExamSection.section_type == 'listening'
    ).first()
    
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    
    # Get listening media for this section
    media = db.query(ListeningMedia)\
        .filter(ListeningMedia.section_id == section.section_id)\
        .first()
    
    # Get questions for this section
    questions = db.query(Question)\
        .filter(Question.section_id == section.section_id)\
        .all()
    
    # Skip the main text question which just holds the transcript
    questions = [q for q in questions if q.question_type != 'main_text']
    
    # Format questions with their options
    formatted_questions = []
    for question in questions:
        # Get options for this question
        options = db.query(QuestionOption)\
            .filter(QuestionOption.question_id == question.question_id)\
            .all()
        
        # Format question data based on question type
        question_data = {
            "question_id": question.question_id,
            "question_type": question.question_type,
            "question_text": question.question_text,
            "correct_answer": question.correct_answer,
            "explanation": question.explanation,
            "locate": question.locate,
            "marks": question.marks
        }
        
        # Add options if they exist
        if options:
            question_data["options"] = [
                {
                    "option_id": opt.option_id,
                    "option_text": opt.option_text,
                    "is_correct": opt.is_correct
                } for opt in options
            ]
        
        formatted_questions.append(question_data)
    
    # Prepare response
    response = {
        "exam_id": exam_id,
        "section_id": section.section_id,
        "part_number": part_number,
        "duration": section.duration,
        "total_marks": section.total_marks,
        "description": section.description,
        "part_title": section.part_title or '',
        "questions": formatted_questions
    }
    
    # Add media data if it exists
    if media:
        response["transcript"] = media.transcript
        response["audio_filename"] = media.audio_filename
    
    return response
@router.get("/users/count", response_model=dict)
async def get_users_count(
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Get the total count of users in the system with optional filtering by role and active status.
    
    Parameters:
    - role: Optional filter by user role ('admin', 'student', 'customer')
    - is_active: Optional filter by active status (True/False)
    
    Returns:
    - Dictionary with total count and filtered parameters
    """
    query = db.query(User)
    
    # Apply filters if provided
    if role:
        query = query.filter(User.role == role)
    
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    
    # Get the count
    total_count = query.count()
    
    # Get role-specific counts
    admin_count = db.query(User).filter(User.role == 'admin').count()
    student_count = db.query(User).filter(User.role == 'student').count()
    customer_count = db.query(User).filter(User.role == 'customer').count()
    
    # Get active/inactive counts
    active_count = db.query(User).filter(User.is_active == True).count()
    inactive_count = db.query(User).filter(User.is_active == False).count()
    
    return {
        "total_users": total_count,
        "filters_applied": {
            "role": role,
            "is_active": is_active
        },
        "breakdown": {
            "by_role": {
                "admin": admin_count,
                "student": student_count,
                "customer": customer_count
            },
            "by_status": {
                "active": active_count,
                "inactive": inactive_count
            }
        }
    }
@router.put("/listening-test/{exam_id}/part/{part_number}/update", response_model=dict)
async def update_listening_part_without_audio(
    exam_id: int,
    part_number: int,
    transcript: Optional[str] = Form(None),
    questions_json: str = Form(...),
    description: Optional[str] = Form(None),
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update a part of a listening test without changing the audio file"""
    
    if not 1 <= part_number <= 4:
        raise HTTPException(status_code=400, detail="Part number must be between 1 and 4")

    try:
        questions_data = json.loads(questions_json)
        if not isinstance(questions_data, list):
            raise HTTPException(status_code=400, detail="Questions data must be an array")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid questions JSON format")

    # Get the section for this part
    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.order_number == part_number
    ).first()
    
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    
    # Update section description if provided
    if description:
        section.description = description

    # Update section description if provided
    if description:
        section.description = description
        db.add(section)

    # Get existing media
    media = db.query(ListeningMedia).filter(
        ListeningMedia.section_id == section.section_id
    ).first()
    
    if not media:
        raise HTTPException(status_code=404, detail="Media not found for this section")

    # Update transcript if provided
    if transcript:
        formatted_transcript = transcript.strip().replace('\r\n', '\n')
        media.transcript = formatted_transcript
        db.add(media)

    # Update section description if provided
    if description:
        section.description = description
        db.add(section)

    # Get existing main text question
    main_question = db.query(Question).filter(
        Question.section_id == section.section_id,
        Question.question_type == 'main_text'
    ).first()
    
    # Update main text question if transcript is provided
    if transcript and main_question:
        main_question.question_text = transcript
        db.add(main_question)
    
    # Delete existing questions (except main text) and their options
    existing_questions = db.query(Question).filter(
        Question.section_id == section.section_id,
        Question.question_type != 'main_text'
    ).all()
    
    for q in existing_questions:
        db.query(QuestionOption).filter(
            QuestionOption.question_id == q.question_id
        ).delete()
        db.delete(q)
    
    # Parse transcript to extract question contexts if transcript is provided
    question_contexts = []
    if transcript:
        soup = BeautifulSoup(transcript, 'html.parser')
        bold_elements = soup.find_all(['strong', 'b'])
        processed_numbers = set()
        
        for bold_elem in bold_elements:
            text = bold_elem.get_text().strip()
            parent = bold_elem.find_parent(['p', 'div', 'td'])
            if parent:
                context = parent.get_text().strip()
                
                if text.isdigit():
                    num = int(text)
                    processed_numbers.add(num)
                    question_contexts.append({
                        'number': num,
                        'context': context,
                        'required_choices': None,
                    })
                elif "Choose THREE" in text or "THREE" in text:
                    for i in range(3):
                        question_contexts.append({
                            'number': None,
                            'context': context,
                            'required_choices': 3,
                        })
                elif "Choose TWO" in text or "TWO" in text:
                    for i in range(2):
                        question_contexts.append({
                            'number': None,
                            'context': context,
                            'required_choices': 2,
                        })
    
    # If no transcript provided or no contexts extracted, use existing questions data directly
    if not question_contexts:
        for q_data in questions_data:
            question = Question(
                section_id=section.section_id,
                question_type=q_data['question_type'],
                question_text=q_data.get('question_text', ''),
                correct_answer=q_data['correct_answer'],
                explanation=q_data.get('explanation', ''),
                locate=q_data.get('locate', ''),
                marks=int(q_data['marks']),
                additional_data={
                    'main_text_id': main_question.question_id if main_question else None,
                    'full_context': transcript if transcript else media.transcript,
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
    else:
        # Process questions with their contexts
        for q_data, context_data in zip(questions_data, question_contexts):
            question = Question(
                section_id=section.section_id,
                question_type=q_data['question_type'],
                question_text=context_data['context'],
                correct_answer=q_data['correct_answer'],
                explanation=q_data.get('explanation', ''),
                locate=q_data.get('locate', ''),
                marks=int(q_data['marks']),
                additional_data={
                    'question_number': context_data['number'],
                    'main_text_id': main_question.question_id if main_question else None,
                    'full_context': transcript if transcript else media.transcript,
                    'required_choices': context_data.get('required_choices'),
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
    
    db.commit()
    
    return {
        "message": f"Part {part_number} updated successfully",
        "section_id": section.section_id,
        "questions_count": len(questions_data)
    }

@router.put("/listening-test/{exam_id}/part/{part_number}/update-with-audio", response_model=dict)
async def update_listening_part_with_audio(
    exam_id: int,
    part_number: int,
    audio_file: UploadFile = File(...),
    transcript: Optional[str] = Form(None),
    questions_json: str = Form(...),
    description: Optional[str] = Form(None),
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update a part of a listening test including the audio file"""
    
    # This is similar to the original update_listening_part endpoint but specifically for when audio is changed
    if not 1 <= part_number <= 4:
        raise HTTPException(status_code=400, detail="Part number must be between 1 and 4")

    try:
        questions_data = json.loads(questions_json)
        if not isinstance(questions_data, list):
            raise HTTPException(status_code=400, detail="Questions data must be an array")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid questions JSON format")

    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.order_number == part_number
    ).first()
    
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Handle audio file
    audio_content = await audio_file.read()
    audio_filename = audio_file.filename

    # Delete existing media and questions
    db.query(ListeningMedia).filter(ListeningMedia.section_id == section.section_id).delete()
    existing_questions = db.query(Question).filter(Question.section_id == section.section_id).all()
    for q in existing_questions:
        db.query(QuestionOption).filter(QuestionOption.question_id == q.question_id).delete()

    db.query(Question).filter(Question.section_id == section.section_id).delete()

    # Parse transcript to extract question contexts
    soup = BeautifulSoup(transcript, 'html.parser')
    bold_elements = soup.find_all(['strong', 'b'])
    question_contexts = []
    processed_numbers = set()
    
    for bold_elem in bold_elements:
        text = bold_elem.get_text().strip()
        parent = bold_elem.find_parent(['p', 'div', 'td'])
        if parent:
            context = parent.get_text().strip()
            
            if text.isdigit():
                num = int(text)
                processed_numbers.add(num)
                question_contexts.append({
                    'number': num,
                    'context': context,
                    'required_choices': None,
                })
            elif "Choose THREE" in text or "THREE" in text:
                for i in range(3):
                    question_contexts.append({
                        'number': None,
                        'context': context,
                        'required_choices': 3,
                    })
            elif "Choose TWO" in text or "TWO" in text:
                for i in range(2):
                    question_contexts.append({
                        'number': None,
                        'context': context,
                        'required_choices': 2,
                    })

    # Upload audio to R2
    from app.utils.r2_storage import upload_audio_to_r2
    r2_key = f"exam_{exam_id}_part_{part_number}_{audio_filename}"
    audio_url = upload_audio_to_r2(audio_content, r2_key)

    # Add new listening media
    formatted_transcript = transcript.strip().replace('\r\n', '\n') if transcript else None
    listening_media = ListeningMedia(
        section_id=section.section_id,
        audio_file=None,
        audio_filename=audio_filename,
        audio_url=audio_url,
        transcript=formatted_transcript,
        duration=section.duration
    )
    db.add(listening_media)
    db.flush()

    # Create main text question for the transcript
    main_question = Question(
        section_id=section.section_id,
        question_type='main_text',
        question_text=transcript,
        additional_data={'part_number': part_number}
    )
    db.add(main_question)
    db.flush()

    # Process questions with their contexts
    for q_data, context_data in zip(questions_data, question_contexts):
        question = Question(
            section_id=section.section_id,
            question_type=q_data['question_type'],
            question_text=context_data['context'],
            correct_answer=q_data['correct_answer'],
            explanation=q_data.get('explanation', ''),
            locate=q_data.get('locate', ''),
            marks=int(q_data['marks']),
            additional_data={
                'question_number': context_data['number'],
                'main_text_id': main_question.question_id,
                'full_context': transcript,
                'required_choices': context_data.get('required_choices'),
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

    db.commit()
    
    return {
        "message": f"Part {part_number} updated successfully with new audio",
        "section_id": section.section_id,
        "filename": audio_filename,
        "questions_count": len(questions_data),
        "questions_found": len(question_contexts)
    }
# ... existing code ...
@router.put("/listening-test/{exam_id}/part/{part_number}", response_model=dict)
async def update_listening_part(
    exam_id: int,
    part_number: int,
    audio_file: UploadFile = File(...),
    transcript: Optional[str] = Form(None),
    questions_json: str = Form(...),
    description: Optional[str] = Form(None),
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    if not 1 <= part_number <= 4:
        raise HTTPException(status_code=400, detail="Part number must be between 1 and 4")

    try:
        questions_data = json.loads(questions_json)
        if not isinstance(questions_data, list):
            raise HTTPException(status_code=400, detail="Questions data must be an array")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid questions JSON format")

    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.order_number == part_number
    ).first()
    
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Handle audio file
    audio_content = await audio_file.read()
    audio_filename = audio_file.filename

    # Delete existing media and questions
    db.query(ListeningMedia).filter(ListeningMedia.section_id == section.section_id).delete()
    existing_questions = db.query(Question).filter(Question.section_id == section.section_id).all()
    for q in existing_questions:
        db.query(QuestionOption).filter(QuestionOption.question_id == q.question_id).delete()
    db.query(Question).filter(Question.section_id == section.section_id).delete()

    # Parse transcript to extract question contexts
    soup = BeautifulSoup(transcript, 'html.parser')
    bold_elements = soup.find_all(['strong', 'b'])
    question_contexts = []
    processed_numbers = set()
    
    for bold_elem in bold_elements:
        text = bold_elem.get_text().strip()
        parent = bold_elem.find_parent(['p', 'div', 'td'])
        if parent:
            context = parent.get_text().strip()
            
            if text.isdigit():
                num = int(text)
                processed_numbers.add(num)
                question_contexts.append({
                    'number': num,
                    'context': context,
                    'required_choices': None,
                    
                })
            elif "Choose THREE" in text or "THREE" in text:
                for i in range(3):
                    question_contexts.append({
                        'number': None,
                        'context': context,
                        'required_choices': 3,
                        
                    })
            elif "Choose TWO" in text or "TWO" in text:
                for i in range(2):
                    question_contexts.append({
                        'number': None,
                        'context': context,
                        'required_choices': 2,
                       
                    })


    # Upload audio to R2
    from app.utils.r2_storage import upload_audio_to_r2
    r2_key = f"exam_{exam_id}_part_{part_number}_{audio_filename}"
    audio_url = upload_audio_to_r2(audio_content, r2_key)

    # Add new listening media
    formatted_transcript = transcript.strip().replace('\r\n', '\n') if transcript else None
    listening_media = ListeningMedia(
        section_id=section.section_id,
        audio_file=None,
        audio_filename=audio_filename,
        audio_url=audio_url,
        transcript=formatted_transcript,
        duration=section.duration
    )
    db.add(listening_media)
    db.flush()

    # Create main text question for the transcript
    main_question = Question(
        section_id=section.section_id,
        question_type='main_text',
        question_text=transcript,
        additional_data={'part_number': part_number}
    )
    db.add(main_question)
    db.flush()

    # Process questions with their contexts
    for q_data, context_data in zip(questions_data, question_contexts):
        question = Question(
            section_id=section.section_id,
            question_type=q_data['question_type'],  # Use the type from frontend
            question_text=context_data['context'],
            correct_answer=q_data['correct_answer'],
            explanation=q_data.get('explanation', ''),
            locate=q_data.get('locate', ''),
            marks=int(q_data['marks']),
            additional_data={
                'question_number': context_data['number'],
                'main_text_id': main_question.question_id,
                'full_context': transcript,
                'required_choices': context_data.get('required_choices'),
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

    # Check if all parts are completed
    completed_parts = db.query(ListeningMedia).join(ExamSection).filter(
        ExamSection.exam_id == exam_id
    ).count()
    
    if completed_parts == 4:
        exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
        exam.is_active = True
        db.add(exam)

    db.commit()
    
    return {
        "message": f"Part {part_number} updated successfully",
        "section_id": section.section_id,
        "filename": audio_filename,
        "questions_count": len(questions_data),
        "questions_found": len(question_contexts)
    }
 

@router.get("/writing-test/{exam_id}/details", response_model=dict)
async def get_writing_test_details(
    exam_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    # Verify exam exists
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    # Get exam section
    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id
    ).first()
    
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Get writing tasks
    writing_tasks = db.query(WritingTask).filter(
        WritingTask.test_id == exam_id
    ).all()

    # Format tasks data
    tasks_data = [{
        "part_number": task.part_number,
        "task_type": task.task_type,
        "title": getattr(task, 'title', None),
        "instructions": task.instructions,
        "sample_essay": getattr(task, 'sample_essay', None),
        "word_limit": task.word_limit,
        "total_marks": task.total_marks,
        "duration": task.duration,
        "is_forecast": getattr(task, 'is_forecast', False),
        "is_recommended": getattr(task, 'is_recommended', False)
    } for task in writing_tasks]

    return {
        "exam_id": exam.exam_id,
        "title": exam.title,
        "created_at": exam.created_at,
        "is_active": exam.is_active,
        "section": {
            "duration": section.duration,
            "total_marks": section.total_marks
        },
        "tasks": tasks_data
    }

 
@router.post("/initialize-writing-test", response_model=dict)
async def initialize_writing_test(
    test_data: WritingTestInit,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    new_exam = Exam(
        title=test_data.title,
        created_at=get_vietnam_time().replace(tzinfo=None),
        is_active=False,  # Set to True when both parts are added
        created_by=current_admin.user_id
    )
    db.add(new_exam)
    db.flush()

    # Create writing section
    writing_section = ExamSection(
        exam_id=new_exam.exam_id,
        section_type='essay',
        duration=60,  # Default duration
        total_marks=40.0,  # Total marks for writing test
        order_number=1
    )
    db.add(writing_section)
    db.commit()

    return {
        "message": "Writing test initialized successfully",
        "exam_id": new_exam.exam_id,
        "title": new_exam.title
    }

@router.post("/writing-test/{exam_id}/part", response_model=dict)
async def add_writing_task(
    exam_id: int,
    task_data: WritingTaskCreate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    if not 1 <= task_data.part_number <= 2:
        raise HTTPException(status_code=400, detail="Part number must be 1 or 2")

    # Verify exam exists and is a writing test
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id
    ).first()
    
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Check if part already exists
    existing_task = db.query(WritingTask).filter(
        WritingTask.test_id == exam_id,
        WritingTask.part_number == task_data.part_number
    ).first()

    if existing_task:
        # Delete existing task if it exists
        db.delete(existing_task)

    # Create new writing task
    writing_task = WritingTask(
        test_id=exam_id,
        part_number=task_data.part_number,
        task_type=task_data.task_type,
        title=task_data.title,
        instructions=task_data.instructions,
        word_limit=task_data.word_limit,
        total_marks=task_data.total_marks,
        duration=task_data.duration,
        is_forecast=task_data.is_forecast,
        sample_essay=task_data.sample_essay
    )
    db.add(writing_task)

    # Check if both parts are completed
    db.flush()
    task_count = db.query(WritingTask).filter(
        WritingTask.test_id == exam_id
    ).count()

    if task_count == 2:
        exam.is_active = True
        section.duration = (
            db.query(func.sum(WritingTask.duration))
            .filter(WritingTask.test_id == exam_id)
            .scalar()
        )
        section.total_marks = (
            db.query(func.sum(WritingTask.total_marks))
            .filter(WritingTask.test_id == exam_id)
            .scalar()
        )
        db.add(exam)
        db.add(section)

    db.commit()

    return {
        "message": f"Writing task part {task_data.part_number} added successfully",
        "exam_id": exam_id,
        "part_number": task_data.part_number,
        "is_complete": task_count == 2
    }
@router.put("/writing-test/{exam_id}", response_model=dict)
async def update_writing_test(
    exam_id: int,
    test_data: WritingTestInit,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    # Verify exam exists and is a writing test
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    # Update exam title
    exam.title = test_data.title
    db.add(exam)
    db.commit()

    return {
        "message": "Writing test updated successfully",
        "exam_id": exam_id,
        "title": exam.title
    }

@router.put("/writing-test/{exam_id}/part/{part_number}", response_model=dict)
async def update_writing_task(
    exam_id: int,
    part_number: int,
    task_data: WritingTaskCreate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    if not 1 <= part_number <= 2:
        raise HTTPException(status_code=400, detail="Part number must be 1 or 2")

    # Verify exam exists
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id
    ).first()
    
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Find existing task
    existing_task = db.query(WritingTask).filter(
        WritingTask.test_id == exam_id,
        WritingTask.part_number == part_number
    ).first()

    if not existing_task:
        raise HTTPException(status_code=404, detail=f"Writing task part {part_number} not found")

    # Update task fields
    existing_task.task_type = task_data.task_type
    existing_task.title = task_data.title
    existing_task.instructions = task_data.instructions
    existing_task.word_limit = task_data.word_limit
    existing_task.total_marks = task_data.total_marks
    existing_task.duration = task_data.duration
    existing_task.is_forecast = task_data.is_forecast
    existing_task.sample_essay = task_data.sample_essay

    # Update section duration and total marks
    section.duration = (
        db.query(func.sum(WritingTask.duration))
        .filter(WritingTask.test_id == exam_id)
        .scalar()
    )
    section.total_marks = (
        db.query(func.sum(WritingTask.total_marks))
        .filter(WritingTask.test_id == exam_id)
        .scalar()
    )

    db.add(existing_task)
    db.add(section)
    db.commit()

    return {
        "message": f"Writing task part {part_number} updated successfully",
        "exam_id": exam_id,
        "part_number": part_number
    }

# Toggle forecast by task id
class ForecastUpdate(BaseModel):
    is_forecast: bool
    title: str | None = None
    is_recommended: bool | None = None

@router.put("/writing-task/{task_id}/forecast", response_model=dict)
async def update_writing_task_forecast(
    task_id: int,
    update: ForecastUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    task = db.query(WritingTask).filter(WritingTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Writing task not found")
    task.is_forecast = update.is_forecast
    if update.title is not None:
        task.title = update.title
    if update.is_recommended is not None:
        task.is_recommended = update.is_recommended
    db.add(task)
    db.commit()
    return {"message": "Forecast updated", "task_id": task_id, "is_forecast": task.is_forecast, "title": task.title, "is_recommended": bool(getattr(task, 'is_recommended', False))}





@router.get("/dashboard/notifications", response_model=List[dict])
async def get_admin_notifications(
    days: int = 30,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get recent notifications for admin dashboard about student activities"""
    
    # Get read notifications for this admin
    read_notifications = db.query(AdminNotificationRead.notification_id)\
        .filter(AdminNotificationRead.admin_id == current_admin.user_id)\
        .all()
    read_notification_ids = [record[0] for record in read_notifications]
    


    # Get recent completed PayPal transactions only (success notifications)
    recent_transactions = db.query(PackageTransaction, User, VIPPackage)\
        .join(User, User.user_id == PackageTransaction.user_id)\
        .join(VIPPackage, VIPPackage.package_id == PackageTransaction.package_id)\
        .filter(
            PackageTransaction.created_at >= get_vietnam_time().replace(tzinfo=None) - timedelta(days=days),
            PackageTransaction.payment_method == "paypal",
            PackageTransaction.status == "completed"
        )\
        .order_by(PackageTransaction.created_at.desc())\
        .limit(50)\
        .all()
    
    notifications = []
    
    # Add transaction notifications
    for transaction, user, package in recent_transactions:
        # Set title/message based on status
        if transaction.status == "completed" and transaction.payment_method == "paypal":
            title = "PayPal Payment Successful"
            message = f"{user.username} purchased {package.name} (${float(transaction.amount):.2f})"
        elif transaction.status == "reject" and transaction.payment_method == "paypal":
            title = "PayPal Payment Failed"
            message = f"{user.username} cancelled {package.name} (${float(transaction.amount):.2f})"
        else:
            title = "VIP Purchase Request"
            message = f"{user.username} requested {package.name} (${float(transaction.amount):.2f})"
        
        notifications.append({
            "id": f"transaction_{transaction.transaction_id}",
            "type": "vip_transaction",
            "title": title,
            "message": message,
            "timestamp": transaction.created_at,
            "user_id": user.user_id,
            "transaction_id": transaction.transaction_id,
            "package_id": package.package_id,
            "payment_method": transaction.payment_method,
            "bank_transfer_image": transaction.bank_transfer_image,
            "amount": float(transaction.amount),
            "status": transaction.status,
            "is_read": f"transaction_{transaction.transaction_id}" in read_notification_ids
        })
    # Sort all notifications by timestamp (newest first)
    notifications.sort(key=lambda x: x["timestamp"], reverse=True)
    
    return notifications
@router.post("/notifications/mark-read", response_model=dict)
async def mark_notifications_read(
    data: NotificationMarkRead,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Mark notifications as read by the admin"""
    
    # Create a new AdminNotificationRead record for each notification
    for notification_id in data.notification_ids:
        # Parse the notification ID to get the type and actual ID
        parts = notification_id.split('_')
        if len(parts) != 2:
            continue
            
        notification_type, item_id = parts
        
        # Check if this notification is already marked as read
        existing = db.query(AdminNotificationRead).filter(
            AdminNotificationRead.admin_id == current_admin.user_id,
            AdminNotificationRead.notification_id == notification_id
        ).first()
        
        if not existing:
            # Create a new read record
            read_record = AdminNotificationRead(
                admin_id=current_admin.user_id,
                notification_id=notification_id,
                notification_type=notification_type,
                item_id=item_id,
                read_at=get_vietnam_time().replace(tzinfo=None)
            )
            db.add(read_record)
    
    db.commit()
    
    return {
        "message": f"Marked {len(data.notification_ids)} notifications as read",
        "notification_ids": data.notification_ids
    }
@router.put("/ielts-exams/{exam_id}/status", response_model=dict)
async def update_exam_status(
    exam_id: int,
    active: bool,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Enable or disable an exam"""
    
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    exam.is_active = active
    db.commit()
    
    return {
        "message": f"Exam {'activated' if active else 'deactivated'} successfully",
        "exam_id": exam.exam_id,
        "title": exam.title,
        "is_active": exam.is_active
    }
@router.get("/ielts-exams", response_model=List[dict])
async def get_ielts_exams(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    exams = db.query(Exam).all()
    return [{"exam_id": exam.exam_id, "title": exam.title, "created_at": exam.created_at, "is_active": exam.is_active} for exam in exams]

@router.delete("/delete-test/{exam_id}", response_model=dict)
async def delete_test(
    exam_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Test not found")

    # Delete all related records
    
    # First delete exam access types
    db.query(ExamAccessType).filter(ExamAccessType.exam_id == exam_id).delete()
    
    # Delete writing tasks
    db.query(WritingTask).filter(WritingTask.test_id == exam_id).delete()
    
    # Delete exam results and their related student answers
    exam_results = db.query(ExamResult).filter(ExamResult.exam_id == exam_id).all()
    for result in exam_results:
        # Delete student answers associated with this result
        db.query(StudentAnswer).filter(StudentAnswer.result_id == result.result_id).delete()
    
    # Now delete the exam results
    db.query(ExamResult).filter(ExamResult.exam_id == exam_id).delete()
    
    sections = db.query(ExamSection).filter(ExamSection.exam_id == exam_id).all()
    for section in sections:
        # First get all questions for this section
        questions = db.query(Question).filter(Question.section_id == section.section_id).all()
        
        # Delete question options first (child records)
        for question in questions:
            db.query(QuestionOption).filter(QuestionOption.question_id == question.question_id).delete()
        
        # Now it's safe to delete questions
        db.query(Question).filter(Question.section_id == section.section_id).delete()
        
        # Delete question groups
        db.query(QuestionGroup).filter(QuestionGroup.section_id == section.section_id).delete()
        
        # Delete any media or passages related to this section
        db.query(ListeningMedia).filter(ListeningMedia.section_id == section.section_id).delete()
        db.query(ReadingPassage).filter(ReadingPassage.section_id == section.section_id).delete()
    
    # Now it's safe to delete the sections
    db.query(ExamSection).filter(ExamSection.exam_id == exam_id).delete()

    # Finally delete the exam
    db.query(Exam).filter(Exam.exam_id == exam_id).delete()

    db.commit()
    
    return {
        "message": "Test deleted successfully",
        "exam_id": exam_id
    }

@router.get("/ielts-exam/{exam_id}", response_model=dict)
async def get_ielts_exam_detail(
    exam_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    sections = []
    for section in exam.exam_sections:
        questions = []
        for question in section.questions:
            options = [{"option_id": opt.option_id, "text": opt.option_text} 
                      for opt in question.options]
            questions.append({
                "question_id": question.question_id,
                "text": question.question_text,
                "type": question.question_type,
                "marks": question.marks,
                "options": options
            })
        
        sections.append({
            "section_id": section.section_id,
            "type": section.section_type,
            "duration": section.duration,
            "section_type": section.section_type,
            "total_marks": section.total_marks,
            "questions": questions
        })

    return {
        "exam_id": exam.exam_id,
        "description": exam.description,
        "title": exam.title,
        "created_at": exam.created_at,
        "sections": sections
    }

# Add these endpoints after the existing ones

@router.get("/writing", response_model=List[dict])
async def get_writing_tests(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    # Get all writing tests with their tasks
    writing_tests = db.query(Exam)\
        .join(ExamSection)\
        .filter(ExamSection.section_type == 'essay')\
        .all()
    
    result = []
    for test in writing_tests:
        tasks = db.query(WritingTask)\
            .filter(WritingTask.test_id == test.exam_id)\
            .order_by(WritingTask.part_number)\
            .all()
            
        # Count total submissions for this test
        submission_count = db.query(func.count(distinct(WritingAnswer.user_id)))\
            .join(WritingTask, WritingTask.task_id == WritingAnswer.task_id)\
            .filter(WritingTask.test_id == test.exam_id)\
            .scalar()
        
        result.append({
            "test_id": test.exam_id,
            "title": test.title,
            "created_at": test.created_at,
            "is_active": test.is_active,
            "total_submissions": submission_count or 0,
            "parts": [{
                "task_id": task.task_id,
                "part_number": task.part_number,
                "task_type": task.task_type,
                "title": getattr(task, 'title', None),
                "word_limit": task.word_limit,
                "duration": task.duration,
                "total_marks": task.total_marks,
                "is_forecast": getattr(task, 'is_forecast', False),
                "is_recommended": getattr(task, 'is_recommended', False)
            } for task in tasks]
        })
    
    return result

@router.get("/students/writing/{test_id}", response_model=List[dict])
async def get_students_with_writing_submissions(
    test_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    # Get all students who have submitted answers for this test
    students = db.query(User).join(WritingAnswer)\
        .join(WritingTask, WritingTask.task_id == WritingAnswer.task_id)\
        .filter(WritingTask.test_id == test_id)\
        .distinct().all()
    
    return [{
        "student_id": student.user_id,
        "username": student.username,
        "email": student.email,
        "last_submission": db.query(WritingAnswer.updated_at)\
            .join(WritingTask)\
            .filter(
                WritingTask.test_id == test_id,
                WritingAnswer.user_id == student.user_id
            )\
            .order_by(WritingAnswer.updated_at.desc())\
            .first()[0]
    } for student in students]


@router.get("/student/{student_id}/writing/{test_id}", response_model=dict)
async def get_student_writing_submission(
    student_id: int,
    test_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    # Get student info
    student = db.query(User).filter(User.user_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Get all tasks and answers for this test
    submissions = db.query(WritingTask, WritingAnswer)\
        .outerjoin(WritingAnswer, and_(
            WritingAnswer.task_id == WritingTask.task_id,
            WritingAnswer.user_id == student_id
        ))\
        .filter(WritingTask.test_id == test_id)\
        .order_by(WritingTask.part_number)\
        .all()

    return {
        "student_id": student.user_id,
        "username": student.username,
        "email": student.email,
        "parts": [{
            "task_id": task.task_id,
            "part_number": task.part_number,
            "task_type": task.task_type,
            "instructions": task.instructions,
            "word_limit": task.word_limit,
            "answer": {
                "answer_text": answer.answer_text if answer else None,
                "score": answer.score if answer else None,
                "submitted_at": answer.created_at if answer else None,
                "last_updated": answer.updated_at if answer else None
            } if answer else None
        } for task, answer in submissions]
    }


# Dashboard endpoints
@router.get("/dashboard/statistics", response_model=dict)
async def get_dashboard_statistics(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get overall system statistics for the admin dashboard"""
    
    # Count total students
    total_students = db.query(func.count(User.user_id)).filter(User.role == 'student').scalar()
    
    # Count total exams
    total_exams = db.query(func.count(Exam.exam_id)).scalar()
    
    # Count active students (with is_active=True)
    active_students = db.query(func.count(User.user_id))\
        .filter(
            User.role == 'student',
            User.is_active == True
        ).scalar()
    
    # Count total exam attempts
    total_attempts = db.query(func.count(ExamResult.result_id)).scalar()
    
    # Count exams by type
    exam_types = db.query(
        ExamSection.section_type,
        func.count(distinct(Exam.exam_id))
    ).join(Exam).group_by(ExamSection.section_type).all()
    
    # Get recent exam results (last 10)
    recent_results = db.query(ExamResult, User, Exam)\
        .join(User, User.user_id == ExamResult.user_id)\
        .join(Exam, Exam.exam_id == ExamResult.exam_id)\
        .order_by(ExamResult.completion_date.desc())\
        .limit(10)\
        .all()
    
    return {
        "total_students": total_students or 0,
        "total_exams": total_exams or 0,
        "active_students": active_students or 0,
        "total_attempts": total_attempts or 0,
        "exam_types": {
            section_type: count for section_type, count in exam_types
        },
        "recent_results": [{
            "result_id": result.result_id,
            "student_name": user.username,
            "exam_title": exam.title,
            "score": result.total_score,
            "completion_date": result.completion_date
        } for result, user, exam in recent_results]
    }
@router.get("/dashboard/students", response_model=List[dict])
async def get_all_students(
    search: Optional[str] = None,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all students with search and hard limit"""
    
    query = db.query(User).filter(User.role == 'student')
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                User.username.ilike(search_term),
                User.email.ilike(search_term)
            )
        )
    
    students = query.order_by(User.user_id).limit(500).all()
    
    return [{
        "user_id": student.user_id,
        "username": student.username,
        "email": student.email,
        "status": getattr(student, 'status', "offline"),
        "is_active": student.is_active,
        "created_at": student.created_at,
        "image_url": student.image_url
    } for student in students]
    
@router.get("/dashboard/student/{student_id}/activity", response_model=dict)
async def get_student_activity(
    student_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get detailed activity for a specific student"""
    
    student = db.query(User).filter(
        User.user_id == student_id,
        User.role == 'student'
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    # Get exam results
    exam_results = db.query(ExamResult, Exam)\
        .join(Exam, Exam.exam_id == ExamResult.exam_id)\
        .filter(ExamResult.user_id == student_id)\
        .order_by(ExamResult.completion_date.desc())\
        .all()
    
    # Get writing submissions
    writing_submissions = db.query(WritingAnswer, WritingTask, Exam)\
        .join(WritingTask, WritingTask.task_id == WritingAnswer.task_id)\
        .join(Exam, Exam.exam_id == WritingTask.test_id)\
        .filter(WritingAnswer.user_id == student_id)\
        .order_by(WritingAnswer.updated_at.desc())\
        .all()
    
    
    return {
        "student": {
            "user_id": student.user_id,
            "username": student.username,
            "email": student.email,
            "status": getattr(student, 'status', "offline"),
            "is_active": student.is_active,
            "created_at": student.created_at
        },
        "exam_results": [{
            "result_id": result.result_id,
            "exam_title": exam.title,
            "score": result.total_score,
            "completion_date": result.completion_date
        } for result, exam in exam_results],
        "writing_submissions": [{
            "answer_id": answer.answer_id,
            "exam_title": exam.title,
            "part_number": task.part_number,
            "task_type": task.task_type,
            "updated_at": answer.updated_at,
            "score": answer.score
        } for answer, task, exam in writing_submissions],
        
    }

@router.put("/dashboard/student/{student_id}/status", response_model=dict)
async def update_student_status(
    student_id: int,
    active: bool,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Enable or disable a student account"""
    
    student = db.query(User).filter(
        User.user_id == student_id,
        User.role == 'student'
    ).first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    student.is_active = active
    db.commit()
    
    return {
        "message": f"Student account {'activated' if active else 'deactivated'} successfully",
        "user_id": student.user_id,
        "is_active": student.is_active
    }

@router.get("/dashboard/exams", response_model=List[dict])
async def get_all_exams(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    exam_type: Optional[str] = None,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all exams — optimized: 3 queries instead of 200+ (N+1 fix)"""
    
    # Subquery: count attempts per exam in ONE query
    attempt_sub = db.query(
        ExamResult.exam_id,
        func.count(ExamResult.result_id).label('attempt_count')
    ).group_by(ExamResult.exam_id).subquery()
    
    # Main query with attempt counts joined
    query = db.query(
        Exam,
        func.coalesce(attempt_sub.c.attempt_count, 0).label('attempts')
    ).outerjoin(
        attempt_sub, attempt_sub.c.exam_id == Exam.exam_id
    )
    
    if search:
        query = query.filter(Exam.title.ilike(f"%{search}%"))
    
    if exam_type:
        query = query.join(ExamSection).filter(ExamSection.section_type == exam_type)
    
    exams_with_attempts = query.order_by(Exam.created_at.desc()).offset(skip).limit(limit).all()
    
    # Batch load ALL section types in ONE query instead of N separate queries
    exam_ids = [exam.exam_id for exam, _ in exams_with_attempts]
    
    if exam_ids:
        sections = db.query(
            ExamSection.exam_id, ExamSection.section_type
        ).filter(
            ExamSection.exam_id.in_(exam_ids)
        ).all()
    else:
        sections = []
    
    # Build lookup map: exam_id -> [section_types]
    section_map = {}
    for exam_id, section_type in sections:
        if exam_id not in section_map:
            section_map[exam_id] = []
        if section_type not in section_map[exam_id]:
            section_map[exam_id].append(section_type)
    
    return [{
        "exam_id": exam.exam_id,
        "title": exam.title,
        "created_at": exam.created_at,
        "is_active": exam.is_active,
        "section_types": section_map.get(exam.exam_id, []),
        "attempts": attempts or 0
    } for exam, attempts in exams_with_attempts]

@router.get("/dashboard/system-logs", response_model=List[dict])
async def get_system_logs(
    days: int = 7,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get system activity logs for monitoring"""
    
    # Get recent active users
    recent_active_users = db.query(User)\
        .filter(
            User.is_active == True,
            User.role == 'student'
        )\
        .order_by(User.created_at.desc())\
        .limit(100)\
        .all()
    
    # Get recent exam submissions
    recent_submissions = db.query(ExamResult, User, Exam)\
        .join(User, User.user_id == ExamResult.user_id)\
        .join(Exam, Exam.exam_id == ExamResult.exam_id)\
        .filter(
            ExamResult.completion_date >= get_vietnam_time().replace(tzinfo=None) - timedelta(days=days)
        )\
        .order_by(ExamResult.completion_date.desc())\
        .limit(100)\
        .all()
    
    logs = []
    
    # Add user logs
    for user in recent_active_users:
        logs.append({
            "timestamp": user.created_at,
            "event_type": "user_created",
            "user_id": user.user_id,
            "username": user.username,
            "details": f"User account created"
        })
    
    # Add submission logs
    for result, user, exam in recent_submissions:
        logs.append({
            "timestamp": result.completion_date,
            "event_type": "exam_submission",
            "user_id": user.user_id,
            "username": user.username,
            "details": f"Completed exam: {exam.title} with score: {result.total_score}"
        })
    
    # Sort logs by timestamp
    logs.sort(key=lambda x: x["timestamp"], reverse=True)
    
    return logs

# ==========================
# VIP History (Admin)
# ==========================

def compute_vip_status(subscriptions: List[VIPSubscription]):
    now = get_vietnam_time().replace(tzinfo=None)
    completed = [s for s in subscriptions if s.payment_status == 'completed']
    if not completed:
        return {
            "vip_active": False,
            "vip_expiry": None
        }
    expiry = max(s.end_date for s in completed) if completed else None
    active = any(s.end_date and s.end_date >= now for s in completed)
    return {
        "vip_active": active,
        "vip_expiry": expiry
    }


@router.get("/vip/history/{user_id}", response_model=dict)
def get_user_vip_history(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get VIP subscription history for a user, including latest transaction and VIP status."""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    subscriptions = (
        db.query(VIPSubscription)
        .filter(VIPSubscription.user_id == user_id)
        .order_by(VIPSubscription.start_date.asc())
        .all()
    )

    history: List[dict] = []
    completed_count = 0
    pending_count = 0
    reject_count = 0

    for s in subscriptions:
        if s.payment_status == 'completed':
            completed_count += 1
        elif s.payment_status == 'pending':
            pending_count += 1
        elif s.payment_status == 'reject':
            reject_count += 1

        package = s.package
        # Get latest transaction for this subscription (if any)
        tx = (
            db.query(PackageTransaction)
            .filter(PackageTransaction.subscription_id == s.subscription_id)
            .order_by(PackageTransaction.created_at.desc())
            .first()
        )

        history.append({
            "subscription_id": s.subscription_id,
            "start_date": s.start_date,
            "end_date": s.end_date,
            "payment_status": s.payment_status,
            "package": {
                "package_id": package.package_id if package else None,
                "name": getattr(package, "name", None) if package else None,
                "duration_months": getattr(package, "duration_months", None) if package else None,
                "price": getattr(package, "price", None) if package else None,
                "package_type": getattr(package, "package_type", None) if package else None,
                "skill_type": getattr(package, "skill_type", None) if package else None,
            },
            "transaction": {
                "transaction_id": tx.transaction_id if tx else None,
                "status": getattr(tx, "status", None) if tx else None,
                "amount": getattr(tx, "amount", None) if tx else None,
                "payment_method": getattr(tx, "payment_method", None) if tx else None,
                "transaction_code": getattr(tx, "transaction_code", None) if tx else None,
                "bank_description": getattr(tx, "bank_description", None) if tx else None,
                "bank_transfer_image": getattr(tx, "bank_transfer_image", None) if tx else None,
                "created_at": getattr(tx, "created_at", None) if tx else None,
                "admin_note": getattr(tx, "admin_note", None) if tx else None,
            } if tx else None,
        })

    status_info = compute_vip_status(subscriptions)

    return {
        "user_id": user.user_id,
        "username": user.username,
        "email": user.email,
        "vip_active": status_info["vip_active"],
        "vip_expiry": status_info["vip_expiry"],
        "counts": {
            "total": len(subscriptions),
            "completed": completed_count,
            "pending": pending_count,
            "reject": reject_count,
        },
        "subscriptions": history,
    }
