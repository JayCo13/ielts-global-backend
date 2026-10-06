"""
Admin Email Broadcast API endpoints.
Allows admin to compose and send bulk emails to non-VIP users.
"""
import threading
import time
import logging
import re
import os
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import and_
from pydantic import BaseModel
from typing import Optional
from app.database import get_db, SessionLocal
from app.models.models import User, EmailBroadcast
from app.routes.admin.auth import get_current_admin
from app.utils.email_service import send_single_email
from app.utils.datetime_utils import get_vietnam_time

logger = logging.getLogger(__name__)
router = APIRouter()

# Simple email validation regex
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# Hard cap on recipients per single broadcast — protects against runaway SES spend.
# Override via env var; defaults to 30,000 (~$3 at $0.10/1000).
MAX_RECIPIENTS_PER_BROADCAST = int(os.getenv("MAX_RECIPIENTS_PER_BROADCAST", "30000"))


class BroadcastRequest(BaseModel):
    subject: str
    body_html: str
    target_filter: str = "non_vip"  # non_vip, all, vip


def _run_broadcast(broadcast_id: int):
    """Background thread to send emails in batches."""
    db = SessionLocal()
    try:
        broadcast = db.query(EmailBroadcast).filter(EmailBroadcast.id == broadcast_id).first()
        if not broadcast:
            logger.error(f"Broadcast {broadcast_id} not found")
            return

        # Build user query based on target filter
        query = db.query(User.email).filter(User.is_active == True)
        if broadcast.target_filter == 'non_vip':
            query = query.filter(and_(
                User.is_vip == False,
                User.email.isnot(None),
                User.email != ''
            ))
        elif broadcast.target_filter == 'vip':
            query = query.filter(and_(
                User.is_vip == True,
                User.email.isnot(None),
                User.email != ''
            ))
        else:  # all
            query = query.filter(and_(
                User.email.isnot(None),
                User.email != ''
            ))

        emails = [row[0] for row in query.all() if row[0] and EMAIL_REGEX.match(row[0])]

        broadcast.total_recipients = len(emails)
        broadcast.status = 'sending'
        db.commit()

        sent = 0
        failed = 0
        batch_size = 40  # send 40 at a time, then pause
        delay_between_batches = 3  # seconds between batches

        for i, email in enumerate(emails):
            try:
                success = send_single_email(email, broadcast.subject, broadcast.body_html)
                if success:
                    sent += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"Error sending to {email}: {e}")
                failed += 1

            # Update progress every 10 emails
            if (i + 1) % 10 == 0:
                broadcast.sent_count = sent
                broadcast.failed_count = failed
                db.commit()

            # Pause between batches to avoid rate limiting
            if (i + 1) % batch_size == 0:
                time.sleep(delay_between_batches)

        # Final update
        broadcast.sent_count = sent
        broadcast.failed_count = failed
        broadcast.status = 'completed'
        broadcast.completed_at = get_vietnam_time().replace(tzinfo=None)
        db.commit()

        logger.info(f"Broadcast {broadcast_id} completed: {sent} sent, {failed} failed out of {len(emails)}")

    except Exception as e:
        logger.error(f"Broadcast {broadcast_id} failed: {e}")
        try:
            broadcast = db.query(EmailBroadcast).filter(EmailBroadcast.id == broadcast_id).first()
            if broadcast:
                broadcast.status = 'failed'
                db.commit()
        except:
            pass
    finally:
        db.close()


@router.get("/email/recipients-count")
async def get_recipients_count(
    target_filter: str = "non_vip",
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get the count of recipients that would receive the email."""
    query = db.query(User).filter(User.is_active == True)
    if target_filter == 'non_vip':
        query = query.filter(and_(User.is_vip == False, User.email.isnot(None), User.email != ''))
    elif target_filter == 'vip':
        query = query.filter(and_(User.is_vip == True, User.email.isnot(None), User.email != ''))
    else:
        query = query.filter(and_(User.email.isnot(None), User.email != ''))

    count = query.count()
    return {"count": count, "target_filter": target_filter}


@router.post("/email/broadcast")
async def create_broadcast(
    req: BroadcastRequest,
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create and start a broadcast email campaign."""
    if not req.subject or not req.body_html:
        raise HTTPException(status_code=400, detail="Subject and body are required")

    # Check if there's already a broadcast sending
    active = db.query(EmailBroadcast).filter(EmailBroadcast.status == 'sending').first()
    if active:
        raise HTTPException(status_code=409, detail="Another broadcast is currently in progress. Please wait for it to complete.")

    # Enforce recipient cap before allocating any work or fetching email rows.
    # Uses .count() so we don't pull the full address list into memory just to size it.
    count_query = db.query(User).filter(User.is_active == True)
    if req.target_filter == 'non_vip':
        count_query = count_query.filter(and_(User.is_vip == False, User.email.isnot(None), User.email != ''))
    elif req.target_filter == 'vip':
        count_query = count_query.filter(and_(User.is_vip == True, User.email.isnot(None), User.email != ''))
    else:
        count_query = count_query.filter(and_(User.email.isnot(None), User.email != ''))
    recipient_count = count_query.count()
    if recipient_count > MAX_RECIPIENTS_PER_BROADCAST:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Recipient count {recipient_count} exceeds the per-broadcast cap of "
                f"{MAX_RECIPIENTS_PER_BROADCAST}. Narrow the target filter or raise "
                f"MAX_RECIPIENTS_PER_BROADCAST in the backend env."
            ),
        )

    # Create broadcast record
    broadcast = EmailBroadcast(
        subject=req.subject,
        body_html=req.body_html,
        target_filter=req.target_filter,
        status='pending',
        created_by=current_admin.user_id
    )
    db.add(broadcast)
    db.commit()
    db.refresh(broadcast)

    # Start background thread
    thread = threading.Thread(target=_run_broadcast, args=(broadcast.id,), daemon=True)
    thread.start()

    return {
        "message": "Broadcast started",
        "broadcast_id": broadcast.id,
        "status": "pending"
    }


@router.get("/email/broadcast/status")
async def get_broadcast_status(
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get the status of the latest/active broadcast."""
    broadcast = db.query(EmailBroadcast).order_by(EmailBroadcast.id.desc()).first()
    if not broadcast:
        return {"active": False}

    return {
        "active": broadcast.status == 'sending',
        "id": broadcast.id,
        "subject": broadcast.subject,
        "status": broadcast.status,
        "target_filter": broadcast.target_filter,
        "total_recipients": broadcast.total_recipients,
        "sent_count": broadcast.sent_count,
        "failed_count": broadcast.failed_count,
        "created_at": broadcast.created_at.isoformat() if broadcast.created_at else None,
        "completed_at": broadcast.completed_at.isoformat() if broadcast.completed_at else None
    }


@router.get("/email/broadcast/history")
async def get_broadcast_history(
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get history of all broadcast campaigns."""
    broadcasts = db.query(EmailBroadcast).order_by(EmailBroadcast.id.desc()).limit(20).all()
    return [{
        "id": b.id,
        "subject": b.subject,
        "status": b.status,
        "target_filter": b.target_filter,
        "total_recipients": b.total_recipients,
        "sent_count": b.sent_count,
        "failed_count": b.failed_count,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "completed_at": b.completed_at.isoformat() if b.completed_at else None
    } for b in broadcasts]


@router.post("/email/test")
async def send_test_email(
    req: BroadcastRequest,
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Send a test email to the admin's own email address."""
    admin_email = current_admin.email
    if not admin_email:
        raise HTTPException(status_code=400, detail="Admin email not found")

    success = send_single_email(admin_email, f"[TEST] {req.subject}", req.body_html)
    if success:
        return {"message": f"Test email sent to {admin_email}"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send test email. Check backend logs for the SES error (likely sandbox mode requiring recipient verification, missing/incorrect SES env vars, or wrong region).")


@router.post("/email/upload-image")
async def upload_email_image(
    file: UploadFile = File(...),
    current_admin=Depends(get_current_admin),
):
    """Upload an image for use in broadcast emails. Returns an absolute public URL.

    Global stores it in Cloudflare R2 (Koyeb's disk is ephemeral, and email clients
    need an absolute URL anyway). Validated by magic bytes, not the client's type.
    """
    from app.utils.upload_security import validate_image_bytes
    from app.utils.r2_storage import upload_image_to_r2

    data = await file.read()
    ext = validate_image_bytes(data)
    filename = f"email-images/{uuid4().hex}{ext}"
    content_type = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
                    ".webp": "image/webp", ".bmp": "image/bmp"}.get(ext, "application/octet-stream")
    try:
        image_url = upload_image_to_r2(data, filename, content_type)
    except Exception as e:
        logger.error(f"Email image upload to R2 failed: {e}")
        raise HTTPException(status_code=502, detail="Image upload failed")
    return {"url": image_url, "filename": filename}
