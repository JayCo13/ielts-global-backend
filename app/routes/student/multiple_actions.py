from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import UserNotification, UpdateKey, Feedback
from app.routes.admin.auth import get_current_admin
from app.routes.admin.auth import get_current_user
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

router = APIRouter()

class FeedbackResponse(BaseModel):
    feedback_id: int
    content: str
    image_url: Optional[str] = None
    created_at: datetime
    
    class Config:
        orm_mode = True

# Pydantic models for response validation
class UserNotificationResponse(BaseModel):
    notification_id: int
    content: str
    type: str
    image_url: Optional[str] = None
    created_at: datetime
    is_active: bool
    
    class Config:
        orm_mode = True

class UpdateKeyResponse(BaseModel):
    key_id: int
    key: str
    type: str
    created_at: datetime
    is_active: bool
    
    class Config:
        orm_mode = True

# GET endpoints for UserNotification
@router.get("/user-notifications", response_model=List[UserNotificationResponse])
async def get_user_notifications(

    db: Session = Depends(get_db)
):
    """Get all active notifications"""
    notifications = db.query(UserNotification).filter(
        UserNotification.is_active == True
    ).order_by(UserNotification.created_at.desc()).all()
    
    return notifications

@router.get("/user-notification/{notification_id}", response_model=UserNotificationResponse)
async def get_user_notification(
    notification_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific notification by ID"""
    notification = db.query(UserNotification).filter(
        UserNotification.notification_id == notification_id,
        UserNotification.is_active == True
    ).first()
    
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    
    return notification

# GET endpoints for UpdateKey
@router.get("/update-keys", response_model=List[UpdateKeyResponse])
async def get_update_keys(
    db: Session = Depends(get_db)
):
    """Get all active update keys"""
    keys = db.query(UpdateKey).filter(
        UpdateKey.is_active == True
    ).order_by(UpdateKey.created_at.desc()).all()
    
    return keys

@router.get("/update-key/{key_id}", response_model=UpdateKeyResponse)
async def get_update_key(
    key_id: int,
    db: Session = Depends(get_db)
):
    """Get a specific update key by ID"""
    key = db.query(UpdateKey).filter(
        UpdateKey.key_id == key_id,
        UpdateKey.is_active == True
    ).first()
    
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Update key not found"
        )
    
    return key

@router.get("/feedbacks", response_model=List[FeedbackResponse])
async def get_feedbacks(
    limit: Optional[int] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Feedback).order_by(Feedback.created_at.desc())
    if limit:
        query = query.limit(limit)
    return query.all()
