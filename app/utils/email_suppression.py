"""Marketing unsubscribe links and the suppression list.

Every broadcast email gets a personal unsubscribe link in the footer plus the
List-Unsubscribe / List-Unsubscribe-Post headers (Gmail and Yahoo require one-click
unsubscribe from bulk senders). The link carries the address and an HMAC of it, so
nothing has to be stored before sending.

Suppressed addresses (unsubscribed, hard bounced, marked as spam) are skipped by
utils/recipient_filter.py. Transactional mail (codes, password resets) ignores the list.

Env:
  PUBLIC_API_URL   public base URL of this backend, used in the unsubscribe link
"""
import hashlib
import hmac
import logging
import os
from urllib.parse import quote

from sqlalchemy.exc import IntegrityError

from app.models.models import EmailSuppression
from app.routes.admin.auth import SECRET_KEY

logger = logging.getLogger(__name__)

PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "https://double-hetti-jaydenco1-dee125d7.koyeb.app").rstrip("/")


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def unsubscribe_token(email: str) -> str:
    msg = f"unsubscribe:{_norm(email)}".encode()
    return hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:32]


def check_unsubscribe_token(email: str, token: str) -> bool:
    return bool(email) and hmac.compare_digest(unsubscribe_token(email), token or "")


def unsubscribe_url(email: str) -> str:
    email = _norm(email)
    return f"{PUBLIC_API_URL}/customer/email/unsubscribe?e={quote(email)}&t={unsubscribe_token(email)}"


def unsubscribe_headers(email: str) -> dict:
    return {
        "List-Unsubscribe": f"<{unsubscribe_url(email)}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def with_unsubscribe_footer(body_html: str, email: str) -> str:
    footer = (
        '<div style="margin:24px auto 0;max-width:600px;padding:16px;text-align:center;'
        'font-family:Arial,sans-serif;font-size:12px;line-height:18px;color:#8a8f98;">'
        'You are receiving this email because you have an account at EnglishOnComputer.<br>'
        f'<a href="{unsubscribe_url(email)}" style="color:#8a8f98;text-decoration:underline;">'
        'Unsubscribe</a> from marketing emails.</div>'
    )
    idx = body_html.lower().rfind("</body>")
    if idx == -1:
        return body_html + footer
    return body_html[:idx] + footer + body_html[idx:]


def suppressed_emails(db) -> set:
    return {r[0] for r in db.query(EmailSuppression.email).all()}


def suppress(db, email: str, reason: str) -> None:
    """Add an address to the suppression list (no-op if it is already there)."""
    email = _norm(email)
    if not email or db.query(EmailSuppression.id).filter(EmailSuppression.email == email).first():
        return
    db.add(EmailSuppression(email=email, reason=reason))
    try:
        db.commit()
        logger.info(f"Suppressed {email} from marketing ({reason})")
    except IntegrityError:
        db.rollback()  # added concurrently
