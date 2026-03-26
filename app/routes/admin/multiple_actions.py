from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import UserNotification, UpdateKey
from app.routes.admin.auth import get_current_admin
from pydantic import BaseModel
import os
from uuid import uuid4
from typing import Optional, List
from datetime import datetime
from app.enums.enums import NotificationTypeEnum, KeyTypeEnum

router = APIRouter()

# Pydantic models for request validation
class UserNotificationUpdate(BaseModel):
    content: str
    type: NotificationTypeEnum
    image_url: Optional[str] = None
    is_active: bool

class UpdateKeyUpdate(BaseModel):
    type: KeyTypeEnum
    is_active: bool
    key: str

# Pydantic models for response validation
class UserNotificationResponse(BaseModel):
    notification_id: int
    content: str
    type: str
    image_url: Optional[str] = None
    is_active: bool
    created_at: datetime

    
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
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all user notifications (admin access)"""
    notifications = db.query(UserNotification).order_by(UserNotification.created_at.desc()).all()
    
    return notifications

@router.get("/user-notification/{notification_id}", response_model=UserNotificationResponse)
async def get_user_notification(
    notification_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get a specific notification by ID (admin access)"""
    notification = db.query(UserNotification).filter(
        UserNotification.notification_id == notification_id
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
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all update keys (admin access)"""
    keys = db.query(UpdateKey).order_by(UpdateKey.created_at.desc()).all()
    
    return keys

@router.get("/update-key/{key_id}", response_model=UpdateKeyResponse)
async def get_update_key(
    key_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get a specific update key by ID (admin access)"""
    key = db.query(UpdateKey).filter(
        UpdateKey.key_id == key_id
    ).first()
    
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Update key not found"
        )
    
    return key

@router.put("/user-notification/{notification_id}", response_model=dict)
async def update_user_notification(
    notification_id: int,
    notification_data: UserNotificationUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update the content of a user notification"""
    # Check if notification exists
    notification = db.query(UserNotification).filter(
        UserNotification.notification_id == notification_id
    ).first()
    
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    
    # Update the content and is_active status
    notification.content = notification_data.content
    notification.type = notification_data.type
    notification.image_url = notification_data.image_url
    notification.is_active = notification_data.is_active
    
    db.commit()
    db.refresh(notification)
    
    return {
        "message": "Notification updated successfully",
        "notification_id": notification.notification_id,
        "content": notification.content,
        "type": notification.type,
        "image_url": notification.image_url,
        "is_active": notification.is_active
    }

@router.put("/update-key/{key_id}", response_model=dict)
async def update_key(
    key_id: int,
    key_data: UpdateKeyUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update the key value of an update key"""
    # Check if key exists
    update_key = db.query(UpdateKey).filter(
        UpdateKey.key_id == key_id
    ).first()
    
    if not update_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Update key not found"
        )
    
    # Update the key
    update_key.key = key_data.key
    update_key.is_active = key_data.is_active
    update_key.type = key_data.type
    
    db.commit()
    db.refresh(update_key)
    
    return {
        "message": "Update key updated successfully",
        "key_id": update_key.key_id,
        "type": update_key.type,
        "is_active": update_key.is_active,
        "key": update_key.key
    }

# Pydantic models for creating new records
class UserNotificationCreate(BaseModel):
    content: str
    type: NotificationTypeEnum
    image_url: Optional[str] = None
    is_active: bool

class UpdateKeyCreate(BaseModel):
    key: str
    type: str

# POST endpoint for creating a new UserNotification
@router.post("/user-notification", response_model=UserNotificationResponse)
async def create_user_notification(
    notification_data: UserNotificationCreate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
   
     # Check if key already exists
    existing_noti = db.query(UserNotification).filter(UserNotification.content == notification_data.content).first()
    
    if existing_noti:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notification already exists"
        )
    
    
    # Create new notification
    new_notification = UserNotification(
        content=notification_data.content,
        type=notification_data.type,
        image_url=notification_data.image_url,
        is_active=notification_data.is_active
    )
    
    db.add(new_notification)
    db.commit()
    db.refresh(new_notification)
    
    return new_notification

# POST endpoint for creating a new UpdateKey
@router.post("/update-key", response_model=UpdateKeyResponse)
async def create_update_key(
    key_data: UpdateKeyCreate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a new update key (admin access)"""
    # Check if key already exists
    existing_key = db.query(UpdateKey).filter(UpdateKey.key == key_data.key).first()
    
    if existing_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Key already exists"
        )
    
    # Create new update key
    new_key = UpdateKey(
        key=key_data.key,
        type=key_data.type,
        is_active=True
    )
    
    db.add(new_key)
    db.commit()
    db.refresh(new_key)
    
    return new_key

# DELETE endpoint for UserNotification
@router.delete("/user-notification/{notification_id}", response_model=dict)
async def delete_user_notification(
    notification_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Delete a user notification by ID (admin access)"""
    # Check if notification exists
    notification = db.query(UserNotification).filter(
        UserNotification.notification_id == notification_id
    ).first()
    
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    
    # Delete the notification
    db.delete(notification)
    db.commit()
    
    return {
        "message": "Notification deleted successfully",
        "notification_id": notification_id
    }

# DELETE endpoint for UpdateKey
@router.delete("/update-key/{key_id}", response_model=dict)
async def delete_update_key(
    key_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Delete an update key by ID (admin access)"""
    # Check if key exists
    update_key = db.query(UpdateKey).filter(
        UpdateKey.key_id == key_id
    ).first()
    
    if not update_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Update key not found"
        )
    
    # Delete the update key
    db.delete(update_key)
    db.commit()
    
    return {
        "message": "Update key deleted successfully",
        "key_id": key_id
    }

# Define the upload directory for notification images
NOTIFICATION_IMAGES_DIR = "static/notification_images"

@router.post("/upload-image", response_model=dict)
async def upload_notification_image(
    image: UploadFile = File(...),
    current_admin = Depends(get_current_admin)
):
    """Upload an image for notifications and return the URL"""
    
    # Validate file type (only allow images)
    content_type = image.content_type
    if not content_type.startswith('image/'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only image files are allowed"
        )
    
    # Create directory if it doesn't exist
    os.makedirs(NOTIFICATION_IMAGES_DIR, exist_ok=True)
    
    # Generate a unique filename
    file_extension = os.path.splitext(image.filename)[1]
    unique_filename = f"{uuid4()}{file_extension}"
    file_path = os.path.join(NOTIFICATION_IMAGES_DIR, unique_filename)
    
    # Save the file
    with open(file_path, "wb") as buffer:
        content = await image.read()
        buffer.write(content)
    
    # Return the URL path that can be used to access the image
    image_url = f"/static/notification_images/{unique_filename}"
    
    return {
        "message": "Image uploaded successfully",
        "image_url": image_url
    }
# Add these imports if not already present
from app.models.models import Feedback

# Pydantic models for Feedback
class FeedbackResponse(BaseModel):
    feedback_id: int
    content: str
    image_url: Optional[str] = None
    created_at: datetime
    
    class Config:
        orm_mode = True

class FeedbackCreate(BaseModel):
    content: str
    image_url: Optional[str] = None

class FeedbackUpdate(BaseModel):
    content: str
    image_url: Optional[str] = None

# Define the upload directory for feedback images
FEEDBACK_IMAGES_DIR = "static/feedback_images"

@router.post("/upload-feedback-image", response_model=dict)
async def upload_feedback_image(
    image: UploadFile = File(...),
    current_admin = Depends(get_current_admin)
):
    """Upload an image for feedback and return the URL"""
    
    # Validate file type (only allow images)
    content_type = image.content_type
    if not content_type.startswith('image/'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only image files are allowed"
        )
    
    # Create directory if it doesn't exist
    os.makedirs(FEEDBACK_IMAGES_DIR, exist_ok=True)
    
    # Generate a unique filename
    file_extension = os.path.splitext(image.filename)[1]
    unique_filename = f"{uuid4()}{file_extension}"
    file_path = os.path.join(FEEDBACK_IMAGES_DIR, unique_filename)
    
    # Save the file
    with open(file_path, "wb") as buffer:
        content = await image.read()
        buffer.write(content)
    
    # Return the URL path that can be used to access the image
    image_url = f"/static/feedback_images/{unique_filename}"
    
    return {
        "message": "Image uploaded successfully",
        "image_url": image_url
    }

# GET endpoints for Feedback
@router.get("/feedbacks", response_model=List[FeedbackResponse])
async def get_feedbacks(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all feedback entries (admin access)"""
    feedbacks = db.query(Feedback).order_by(Feedback.created_at.desc()).all()
    
    return feedbacks

@router.get("/feedback/{feedback_id}", response_model=FeedbackResponse)
async def get_feedback(
    feedback_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get a specific feedback by ID (admin access)"""
    feedback = db.query(Feedback).filter(
        Feedback.feedback_id == feedback_id
    ).first()
    
    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found"
        )
    
    return feedback

# POST endpoint for creating a new Feedback
@router.post("/feedback", response_model=FeedbackResponse)
async def create_feedback(
    feedback_data: FeedbackCreate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a new feedback entry (admin access)"""
    # Create new feedback
    new_feedback = Feedback(
        content=feedback_data.content,
        image_url=feedback_data.image_url
    )
    
    db.add(new_feedback)
    db.commit()
    db.refresh(new_feedback)
    
    return new_feedback

# PUT endpoint for updating Feedback
@router.put("/feedback/{feedback_id}", response_model=FeedbackResponse)
async def update_feedback(
    feedback_id: int,
    feedback_data: FeedbackUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update a feedback entry (admin access)"""
    # Check if feedback exists
    feedback = db.query(Feedback).filter(
        Feedback.feedback_id == feedback_id
    ).first()
    
    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found"
        )
    
    # Update the feedback
    feedback.content = feedback_data.content
    if feedback_data.image_url is not None:
        feedback.image_url = feedback_data.image_url
    
    db.commit()
    db.refresh(feedback)
    
    return feedback

# DELETE endpoint for Feedback
@router.delete("/feedback/{feedback_id}", response_model=dict)
async def delete_feedback(
    feedback_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Delete a feedback entry by ID (admin access)"""
    # Check if feedback exists
    feedback = db.query(Feedback).filter(
        Feedback.feedback_id == feedback_id
    ).first()
    
    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found"
        )
    
    # Delete the feedback
    db.delete(feedback)
    db.commit()
    
    return {
        "message": "Feedback deleted successfully",
        "feedback_id": feedback_id
    }
