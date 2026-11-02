"""Public email endpoints: marketing unsubscribe and the Resend event webhook.

Unsubscribe (link in every broadcast footer and in the List-Unsubscribe header):
  GET  /customer/email/unsubscribe?e=&t=   confirmation page with a button
  POST /customer/email/unsubscribe?e=&t=   unsubscribes (the button, and Gmail's
                                           one-click unsubscribe)
GET only shows the page, so link scanners that open every URL in an email cannot
unsubscribe anyone.

Resend webhook (POST /customer/email/resend/webhook): hard bounces and spam
complaints are added to the suppression list. Signed with Svix; the signing secret
from the Resend dashboard goes in RESEND_WEBHOOK_SECRET.
"""
import base64
import hashlib
import hmac
import html
import json
import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils.email_suppression import check_unsubscribe_token, suppress

logger = logging.getLogger(__name__)
router = APIRouter()

WEBHOOK_TOLERANCE_SECONDS = 300


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{title} | EnglishOnComputer</title></head>
<body style="margin:0;background:#f5f7fa;font-family:Arial,sans-serif;color:#07223d;">
<div style="max-width:440px;margin:64px auto;padding:32px 24px;background:#fff;border-radius:12px;
box-shadow:0 2px 12px rgba(7,34,61,.08);text-align:center;">
<h1 style="font-size:20px;margin:0 0 12px;">{title}</h1>{body}
<p style="margin:24px 0 0;font-size:13px;"><a href="https://englishoncomputer.com" style="color:#07223d;">EnglishOnComputer</a></p>
</div></body></html>""")


@router.get("/email/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(e: str = "", t: str = ""):
    if not check_unsubscribe_token(e, t):
        return _page("Invalid link", '<p style="font-size:14px;color:#555;">This unsubscribe link is not valid.</p>')
    email = html.escape(e.strip().lower())
    return _page("Unsubscribe", f"""
<p style="font-size:14px;color:#555;line-height:21px;">Stop marketing emails to <strong>{email}</strong>?
You will still receive account emails such as verification codes and password resets.</p>
<form method="post"><button type="submit" style="margin-top:12px;padding:10px 24px;border:0;border-radius:8px;
background:#07223d;color:#fff;font-size:14px;font-weight:bold;cursor:pointer;">Unsubscribe</button></form>""")


@router.post("/email/unsubscribe", response_class=HTMLResponse)
def unsubscribe(e: str = "", t: str = "", db: Session = Depends(get_db)):
    if not check_unsubscribe_token(e, t):
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link")
    suppress(db, e, "unsubscribe")
    return _page("You are unsubscribed", '<p style="font-size:14px;color:#555;">'
                 'You will no longer receive marketing emails from EnglishOnComputer.</p>')


def _verify_svix(secret: str, msg_id: str, timestamp: str, signatures: str, body: bytes) -> bool:
    try:
        if abs(time.time() - int(timestamp)) > WEBHOOK_TOLERANCE_SECONDS:
            return False
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except (ValueError, TypeError):
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for sig in signatures.split():
        version, _, value = sig.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return True
    return False


@router.post("/email/resend/webhook")
async def resend_webhook(request: Request, db: Session = Depends(get_db)):
    secret = os.getenv("RESEND_WEBHOOK_SECRET", "")
    if not secret:
        logger.error("Resend webhook called but RESEND_WEBHOOK_SECRET is not set")
        raise HTTPException(status_code=503, detail="Webhook not configured")
    body = await request.body()
    if not _verify_svix(secret, request.headers.get("svix-id", ""),
                        request.headers.get("svix-timestamp", ""),
                        request.headers.get("svix-signature", ""), body):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    reason = {"email.bounced": "bounce", "email.complained": "complaint"}.get(event.get("type"))
    if reason:
        to = (event.get("data") or {}).get("to") or []
        for email in ([to] if isinstance(to, str) else to):
            suppress(db, email, reason)
    return {"ok": True}
