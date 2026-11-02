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
from app.utils.recipient_filter import build_recipients
from app.utils.email_suppression import with_unsubscribe_footer, unsubscribe_headers

logger = logging.getLogger(__name__)
router = APIRouter()

# Inboxes that receive the "Send test" email. Override via env var (comma-separated).
BROADCAST_TEST_EMAILS = os.getenv("BROADCAST_TEST_EMAILS", "taicopgm@gmail.com,cotrinhhientai@gmail.com")

# Simple email validation regex
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# Hard cap on recipients per single broadcast — protects against runaway SES spend.
# Override via env var; defaults to 30,000 (~$3 at $0.10/1000).
MAX_RECIPIENTS_PER_BROADCAST = int(os.getenv("MAX_RECIPIENTS_PER_BROADCAST", "30000"))

# Seconds between two broadcast sends. Resend's default limit is a few requests per
# second for the whole team, and OTP / password-reset mail shares it, so stay below.
BROADCAST_SEND_INTERVAL = float(os.getenv("BROADCAST_SEND_INTERVAL", "0.6"))


class BroadcastRequest(BaseModel):
    subject: str
    body_html: str
    target_filter: str = "non_vip"  # non_vip, all, vip
    only_trusted: bool = True  # skip addresses with no sign of a real inbox (see utils/recipient_filter.py)
    max_recipients: Optional[int] = None  # send only to the N most engaged (domain warm-up)


def _split_filter(stored: str):
    """EmailBroadcast.target_filter stores e.g. 'non_vip+trusted'."""
    base, _, flag = (stored or "non_vip").partition("+")
    return base, flag == "trusted"


def _send_marketing(email: str, subject: str, body_html: str) -> bool:
    """One broadcast email with the recipient's unsubscribe link and headers."""
    return send_single_email(email, subject, with_unsubscribe_footer(body_html, email),
                             headers=unsubscribe_headers(email))


def _run_broadcast(broadcast_id: int, max_recipients: Optional[int] = None):
    """Background thread to send emails in batches."""
    db = SessionLocal()
    try:
        broadcast = db.query(EmailBroadcast).filter(EmailBroadcast.id == broadcast_id).first()
        if not broadcast:
            logger.error(f"Broadcast {broadcast_id} not found")
            return

        target_filter, only_trusted = _split_filter(broadcast.target_filter)
        emails, stats = build_recipients(db, target_filter, only_trusted)
        if max_recipients:
            emails = emails[:max_recipients]
        logger.info(f"Broadcast {broadcast_id} recipients after filtering: {stats}, limit {max_recipients}")

        broadcast.total_recipients = len(emails)
        broadcast.status = 'sending'
        db.commit()

        sent = 0
        failed = 0

        for i, email in enumerate(emails):
            started = time.monotonic()
            try:
                success = _send_marketing(email, broadcast.subject, broadcast.body_html)
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

            # Even pacing to stay under the provider's rate limit
            wait = BROADCAST_SEND_INTERVAL - (time.monotonic() - started)
            if wait > 0:
                time.sleep(wait)

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
def get_recipients_count(
    target_filter: str = "non_vip",
    only_trusted: bool = True,
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Count of recipients after filtering, with how many each filter removed.
    Plain def: the domain checks do DNS lookups, so this runs in the threadpool."""
    emails, stats = build_recipients(db, target_filter, only_trusted)
    return {"count": len(emails), "target_filter": target_filter, "only_trusted": only_trusted, "stats": stats}


@router.post("/email/broadcast")
def create_broadcast(
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

    # Enforce recipient cap before allocating any work (same filtering as the send)
    if req.max_recipients is not None and req.max_recipients < 1:
        raise HTTPException(status_code=400, detail="max_recipients must be at least 1")
    recipient_emails, _ = build_recipients(db, req.target_filter, req.only_trusted)
    recipient_count = len(recipient_emails)
    if req.max_recipients:
        recipient_count = min(recipient_count, req.max_recipients)
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
        target_filter=f"{req.target_filter}+trusted" if req.only_trusted else req.target_filter,
        status='pending',
        created_by=current_admin.user_id
    )
    db.add(broadcast)
    db.commit()
    db.refresh(broadcast)

    # Start background thread
    thread = threading.Thread(target=_run_broadcast, args=(broadcast.id, req.max_recipients), daemon=True)
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
    """Send a test email to the fixed test inboxes (BROADCAST_TEST_EMAILS)."""
    recipients = [e.strip() for e in BROADCAST_TEST_EMAILS.split(",") if e.strip()]
    if not recipients:
        raise HTTPException(status_code=400, detail="No test recipients configured")

    sent = [e for e in recipients if _send_marketing(e, f"[TEST] {req.subject}", req.body_html)]
    if sent:
        failed = [e for e in recipients if e not in sent]
        msg = f"Test email sent to {', '.join(sent)}"
        if failed:
            msg += f" (failed: {', '.join(failed)})"
        return {"message": msg}
    else:
        raise HTTPException(status_code=500, detail="Failed to send test email. Check backend logs for the send error.")


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
