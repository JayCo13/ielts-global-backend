from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import User
from pydantic import BaseModel, EmailStr, Field
from datetime import datetime, timedelta
from jose import jwt, JWTError
import os
from app.utils.email_utils import send_password_reset_email
from app.routes.admin.auth import pwd_context, SECRET_KEY, ALGORITHM, create_access_token
from app.utils.datetime_utils import get_vietnam_time
from typing import Optional

router = APIRouter()

# Set token expiration to 30 minutes
RESET_TOKEN_EXPIRE_MINUTES = 30
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(..., min_length=6)
    confirm_password: str

@router.post("/request-password-reset", response_model=dict)
async def request_password_reset(
    request: PasswordResetRequest,
    db: Session = Depends(get_db)
):
    """
    Request a password reset. This will send an email with a reset link to the user.
    Works for both student and customer users.
    """
    # Find the user with the provided email
    user = db.query(User).filter(User.email == request.email).first()
    
    # For security reasons, don't reveal whether the email exists
    if not user or user.role not in ["student", "customer"]:
        return {
            "message": "If the email exists in our system, a password reset link will be sent."
        }
    
    # Create a password reset token
    reset_token_expires = timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    reset_token = create_access_token(
        data={"sub": user.username, "reset": True, "email": user.email},
        expires_delta=reset_token_expires
    )
    
    # Send password reset email
    email_sent = send_password_reset_email(
        to_email=user.email,
        reset_token=reset_token,
        username=user.username,
        frontend_url=FRONTEND_URL
    )
    
    if not email_sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send password reset email. Please try again later."
        )
    
    return {
        "message": "If the email exists in our system, a password reset link will be sent."
    }

@router.post("/reset-password", response_model=dict)
async def reset_password(
    reset_data: PasswordResetConfirm,
    db: Session = Depends(get_db)
):
    """
    Reset the user's password using the reset token.
    """
    # Verify passwords match
    if reset_data.new_password != reset_data.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match"
        )
    
    # Verify token
    try:
        payload = jwt.decode(reset_data.token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Ensure this is a reset token
        if not payload.get("reset"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid reset token"
            )
        
        username = payload.get("sub")
        email = payload.get("email")
        
        if not username or not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid reset token"
            )
            
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token"
        )
    
    # Find the user
    user = db.query(User).filter(
        User.username == username, 
        User.email == email
    ).first()
    
    if not user or user.role not in ["student", "customer"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Update the password
    user.password = pwd_context.hash(reset_data.new_password)
    user.last_active = get_vietnam_time().replace(tzinfo=None)
    
    db.commit()
    
    return {
        "message": "Password has been reset successfully"
    }

@router.get("/verify-reset-token", response_model=dict)
async def verify_reset_token(token: str):
    """
    Verify if a reset token is valid and not expired.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Ensure this is a reset token
        if not payload.get("reset"):
            return {"valid": False}
        
        username = payload.get("sub")
        email = payload.get("email")
        
        if not username or not email:
            return {"valid": False}
            
        return {"valid": True, "username": username, "email": email}
        
    except JWTError:
        return {"valid": False} 