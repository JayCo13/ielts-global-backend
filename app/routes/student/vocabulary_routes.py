from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from app.database import get_db
from app.models.models import SavedVocabulary, User
from app.routes.student.student_actions import get_current_student

router = APIRouter()


class VocabularyCreate(BaseModel):
    word: str
    context: Optional[str] = None
    source_type: str  # 'listening' or 'reading'
    source_exam_id: Optional[int] = None
    source_exam_title: Optional[str] = None


class VocabularyUpdate(BaseModel):
    is_important: bool


class VocabularyResponse(BaseModel):
    id: int
    word: str
    context: Optional[str]
    source_type: str
    source_exam_id: Optional[int]
    source_exam_title: Optional[str]
    is_important: bool
    created_at: str

    class Config:
        from_attributes = True


@router.post("/vocabulary", response_model=VocabularyResponse)
async def add_vocabulary(
    vocab: VocabularyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student)
):
    """Add a new vocabulary word."""
    if vocab.source_type not in ['listening', 'reading']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_type must be 'listening' or 'reading'"
        )
    
    new_vocab = SavedVocabulary(
        user_id=current_user.user_id,
        word=vocab.word,
        context=vocab.context,
        source_type=vocab.source_type,
        source_exam_id=vocab.source_exam_id,
        source_exam_title=vocab.source_exam_title,
        is_important=False
    )
    db.add(new_vocab)
    db.commit()
    db.refresh(new_vocab)
    
    return VocabularyResponse(
        id=new_vocab.id,
        word=new_vocab.word,
        context=new_vocab.context,
        source_type=new_vocab.source_type,
        source_exam_id=new_vocab.source_exam_id,
        source_exam_title=new_vocab.source_exam_title,
        is_important=new_vocab.is_important,
        created_at=new_vocab.created_at.isoformat()
    )


@router.get("/vocabulary", response_model=List[VocabularyResponse])
async def list_vocabulary(
    source_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student)
):
    """List all saved vocabulary for the current user."""
    query = db.query(SavedVocabulary).filter(SavedVocabulary.user_id == current_user.user_id)
    
    if source_type and source_type in ['listening', 'reading']:
        query = query.filter(SavedVocabulary.source_type == source_type)
    
    vocab_list = query.order_by(SavedVocabulary.created_at.desc()).all()
    
    return [
        VocabularyResponse(
            id=v.id,
            word=v.word,
            context=v.context,
            source_type=v.source_type,
            source_exam_id=v.source_exam_id,
            source_exam_title=v.source_exam_title,
            is_important=v.is_important,
            created_at=v.created_at.isoformat()
        )
        for v in vocab_list
    ]


@router.put("/vocabulary/{vocab_id}", response_model=VocabularyResponse)
async def update_vocabulary(
    vocab_id: int,
    update: VocabularyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student)
):
    """Toggle importance of a vocabulary word."""
    vocab = db.query(SavedVocabulary).filter(
        SavedVocabulary.id == vocab_id,
        SavedVocabulary.user_id == current_user.user_id
    ).first()
    
    if not vocab:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vocabulary not found"
        )
    
    vocab.is_important = update.is_important
    db.commit()
    db.refresh(vocab)
    
    return VocabularyResponse(
        id=vocab.id,
        word=vocab.word,
        context=vocab.context,
        source_type=vocab.source_type,
        source_exam_id=vocab.source_exam_id,
        source_exam_title=vocab.source_exam_title,
        is_important=vocab.is_important,
        created_at=vocab.created_at.isoformat()
    )


@router.delete("/vocabulary/{vocab_id}")
async def delete_vocabulary(
    vocab_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student)
):
    """Delete a vocabulary word."""
    vocab = db.query(SavedVocabulary).filter(
        SavedVocabulary.id == vocab_id,
        SavedVocabulary.user_id == current_user.user_id
    ).first()
    
    if not vocab:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vocabulary not found"
        )
    
    db.delete(vocab)
    db.commit()
    
    return {"message": "Vocabulary deleted successfully"}
