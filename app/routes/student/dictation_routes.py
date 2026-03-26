from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import List, Optional
from app.database import get_db
from app.models.models import DictationUnit, DictationWord, User, StudentImportantWord
from app.routes.student.student_actions import get_current_student

router = APIRouter()


class WordResponse(BaseModel):
    word_id: int
    word: str
    order_index: int
    is_important: bool

    class Config:
        from_attributes = True


class UnitListResponse(BaseModel):
    unit_id: int
    name: str
    description: Optional[str]
    word_count: int
    important_count: int

    class Config:
        from_attributes = True


class UnitWordsResponse(BaseModel):
    unit_id: int
    name: str
    description: Optional[str]
    words: List[WordResponse]

    class Config:
        from_attributes = True


@router.get("/dictation/units", response_model=List[UnitListResponse])
async def list_active_units(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student)
):
    """List all active dictation units for students."""
    units = db.query(DictationUnit).filter(
        DictationUnit.is_active == True
    ).order_by(DictationUnit.created_at.desc()).all()
    
    result = []
    for u in units:
        # Count important words for THIS student only
        important_count = db.query(func.count(StudentImportantWord.id)).filter(
            StudentImportantWord.user_id == current_user.user_id,
            StudentImportantWord.word_id.in_([w.word_id for w in u.words])
        ).scalar() or 0
        
        result.append(UnitListResponse(
            unit_id=u.unit_id,
            name=u.name,
            description=u.description,
            word_count=len(u.words),
            important_count=important_count
        ))
    
    return result


@router.get("/dictation/units/{unit_id}/words", response_model=UnitWordsResponse)
async def get_unit_words(
    unit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student)
):
    """Get all words in a unit for dictation practice."""
    unit = db.query(DictationUnit).filter(
        DictationUnit.unit_id == unit_id,
        DictationUnit.is_active == True
    ).first()
    
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    
    words = db.query(DictationWord).filter(
        DictationWord.unit_id == unit_id
    ).order_by(DictationWord.order_index).all()
    
    # Get this student's important word IDs
    important_word_ids = set(
        row[0] for row in db.query(StudentImportantWord.word_id).filter(
            StudentImportantWord.user_id == current_user.user_id,
            StudentImportantWord.word_id.in_([w.word_id for w in words])
        ).all()
    )
    
    return UnitWordsResponse(
        unit_id=unit.unit_id,
        name=unit.name,
        description=unit.description,
        words=[
            WordResponse(
                word_id=w.word_id,
                word=w.word,
                order_index=w.order_index,
                is_important=w.word_id in important_word_ids
            )
            for w in words
        ]
    )


class ToggleImportantRequest(BaseModel):
    is_important: bool


@router.put("/dictation/words/{word_id}/important")
async def toggle_word_important(
    word_id: int,
    request: ToggleImportantRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student)
):
    """Toggle the important status of a word for the current student."""
    word = db.query(DictationWord).filter(DictationWord.word_id == word_id).first()
    
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")
    
    if request.is_important:
        # Add to student's important words (if not already exists)
        existing = db.query(StudentImportantWord).filter(
            StudentImportantWord.user_id == current_user.user_id,
            StudentImportantWord.word_id == word_id
        ).first()
        
        if not existing:
            new_important = StudentImportantWord(
                user_id=current_user.user_id,
                word_id=word_id
            )
            db.add(new_important)
            db.commit()
    else:
        # Remove from student's important words
        db.query(StudentImportantWord).filter(
            StudentImportantWord.user_id == current_user.user_id,
            StudentImportantWord.word_id == word_id
        ).delete()
        db.commit()
    
    return {"success": True, "is_important": request.is_important}
