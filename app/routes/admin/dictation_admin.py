from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from app.database import get_db
from app.models.models import DictationUnit, DictationWord, User
from app.routes.admin.auth import get_current_admin

router = APIRouter()


# Schemas
class UnitCreate(BaseModel):
    name: str
    description: Optional[str] = None


class UnitUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class WordCreate(BaseModel):
    word: str


class WordsAddRequest(BaseModel):
    words: List[str]


class WordUpdate(BaseModel):
    word: Optional[str] = None
    is_important: Optional[bool] = None


class WordResponse(BaseModel):
    word_id: int
    word: str
    order_index: int
    is_important: bool

    class Config:
        from_attributes = True


class UnitResponse(BaseModel):
    unit_id: int
    name: str
    description: Optional[str]
    is_active: bool
    word_count: int
    important_count: int
    created_at: str

    class Config:
        from_attributes = True


class UnitDetailResponse(BaseModel):
    unit_id: int
    name: str
    description: Optional[str]
    is_active: bool
    words: List[WordResponse]
    created_at: str

    class Config:
        from_attributes = True


# Unit endpoints
@router.post("/dictation/units", response_model=UnitResponse)
async def create_unit(
    unit: UnitCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """Create a new dictation unit."""
    new_unit = DictationUnit(
        name=unit.name,
        description=unit.description
    )
    db.add(new_unit)
    db.commit()
    db.refresh(new_unit)
    
    return UnitResponse(
        unit_id=new_unit.unit_id,
        name=new_unit.name,
        description=new_unit.description,
        is_active=new_unit.is_active,
        word_count=0,
        important_count=0,
        created_at=new_unit.created_at.isoformat()
    )


@router.get("/dictation/units", response_model=List[UnitResponse])
async def list_units(
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """List all dictation units."""
    units = db.query(DictationUnit).order_by(DictationUnit.created_at.desc()).all()
    
    return [
        UnitResponse(
            unit_id=u.unit_id,
            name=u.name,
            description=u.description,
            is_active=u.is_active,
            word_count=len(u.words),
            important_count=len([w for w in u.words if w.is_important]),
            created_at=u.created_at.isoformat()
        )
        for u in units
    ]


@router.get("/dictation/units/{unit_id}", response_model=UnitDetailResponse)
async def get_unit(
    unit_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """Get a dictation unit with all its words."""
    unit = db.query(DictationUnit).filter(DictationUnit.unit_id == unit_id).first()
    
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    
    words = db.query(DictationWord).filter(
        DictationWord.unit_id == unit_id
    ).order_by(DictationWord.order_index).all()
    
    return UnitDetailResponse(
        unit_id=unit.unit_id,
        name=unit.name,
        description=unit.description,
        is_active=unit.is_active,
        words=[WordResponse(word_id=w.word_id, word=w.word, order_index=w.order_index, is_important=w.is_important or False) for w in words],
        created_at=unit.created_at.isoformat()
    )


@router.put("/dictation/units/{unit_id}", response_model=UnitResponse)
async def update_unit(
    unit_id: int,
    update: UnitUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """Update a dictation unit."""
    unit = db.query(DictationUnit).filter(DictationUnit.unit_id == unit_id).first()
    
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    
    if update.name is not None:
        unit.name = update.name
    if update.description is not None:
        unit.description = update.description
    if update.is_active is not None:
        unit.is_active = update.is_active
    
    db.commit()
    db.refresh(unit)
    
    return UnitResponse(
        unit_id=unit.unit_id,
        name=unit.name,
        description=unit.description,
        is_active=unit.is_active,
        word_count=len(unit.words),
        important_count=len([w for w in unit.words if w.is_important]),
        created_at=unit.created_at.isoformat()
    )


@router.delete("/dictation/units/{unit_id}")
async def delete_unit(
    unit_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """Delete a dictation unit and all its words."""
    unit = db.query(DictationUnit).filter(DictationUnit.unit_id == unit_id).first()
    
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    
    db.delete(unit)
    db.commit()
    
    return {"message": "Unit deleted successfully"}


# Word endpoints
@router.post("/dictation/units/{unit_id}/words")
async def add_words(
    unit_id: int,
    request: WordsAddRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """Add multiple words to a unit."""
    unit = db.query(DictationUnit).filter(DictationUnit.unit_id == unit_id).first()
    
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    
    # Get current max order
    max_order = db.query(DictationWord).filter(
        DictationWord.unit_id == unit_id
    ).count()
    
    added_words = []
    for i, word_text in enumerate(request.words):
        if word_text.strip():  # Only add non-empty words
            new_word = DictationWord(
                unit_id=unit_id,
                word=word_text.strip(),
                order_index=max_order + i
            )
            db.add(new_word)
            added_words.append(new_word)
    
    db.commit()
    
    return {
        "message": f"Added {len(added_words)} words",
        "words": [{"word_id": w.word_id, "word": w.word, "order_index": w.order_index} for w in added_words]
    }


@router.put("/dictation/words/{word_id}")
async def update_word(
    word_id: int,
    update: WordUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """Update a word."""
    word = db.query(DictationWord).filter(DictationWord.word_id == word_id).first()
    
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")
    
    if update.word is not None:
        word.word = update.word
    if update.is_important is not None:
        word.is_important = update.is_important
    db.commit()
    db.refresh(word)
    
    return {"word_id": word.word_id, "word": word.word, "order_index": word.order_index, "is_important": word.is_important}


@router.delete("/dictation/words/{word_id}")
async def delete_word(
    word_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """Delete a word."""
    word = db.query(DictationWord).filter(DictationWord.word_id == word_id).first()
    
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")
    
    db.delete(word)
    db.commit()
    
    return {"message": "Word deleted successfully"}
