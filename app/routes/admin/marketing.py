from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from app.database import get_db
from app.routes.admin.auth import get_current_user
from app.models.models import User
from app.utils.email_utils import send_email, is_valid_email
from pydantic import BaseModel
import logging
import time

logger = logging.getLogger(__name__)

router = APIRouter()

class MarketingEmailRequest(BaseModel):
    subject: str
    html_content: str

def check_is_admin(current_user: User):
    if current_user.role != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges"
        )
    return current_user

def process_marketing_emails(subject: str, html_content: str, db: Session):
    try:
        users = db.query(User).filter(User.email.isnot(None)).all()
        success_count = 0
        fail_count = 0
        
        for user in users:
            if user.email and is_valid_email(user.email):
                try:
                    success = send_email(user.email, subject, html_content)
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    logger.error(f"Failed to send marketing email to {user.email}: {e}")
                    fail_count += 1
                # Small sleep to prevent rate limiting
                time.sleep(0.5)
                
        logger.info(f"Marketing email broadcast finished. Success: {success_count}, Failed: {fail_count}")
    except Exception as e:
        logger.error(f"Error in background task process_marketing_emails: {e}")

@router.post("/send-email")
async def send_marketing_email(
    request: MarketingEmailRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    check_is_admin(current_user)
    
    # We pass the db session. Note: in background tasks, passing the same session might cause issues if the request session closes.
    # We should get a new session for the background task or fetch emails first.
    users = db.query(User.email).filter(User.email.isnot(None)).all()
    target_emails = [u.email for u in users if u.email and is_valid_email(u.email)]
    
    if not target_emails:
        raise HTTPException(status_code=400, detail="No valid user emails found.")

    def background_sender(emails, subj, html):
        success_count = 0
        fail_count = 0
        for email in emails:
            try:
                success = send_email(email, subj, html)
                if success:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"Failed to send to {email}: {e}")
                fail_count += 1
            time.sleep(0.5)
        logger.info(f"Broadcast complete. Success: {success_count}, Failed: {fail_count}")

    background_tasks.add_task(background_sender, target_emails, request.subject, request.html_content)
    
    return {
        "message": "Broadcast started in the background",
        "target_audience_size": len(target_emails)
    }
