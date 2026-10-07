"""Thin Resend HTTPS API client for outbound email.

Both the transactional path (app/utils/email_utils.py) and the marketing/
broadcast path (app/utils/email_service.py) prefer Resend when RESEND_API_KEY
is set, and fall back to their previous transport (Gmail SMTP / AWS SES) when
it isn't — so deploying this is a no-op until the key is configured.

Transactional and marketing use DIFFERENT From identities so a marketing spam
complaint can't drag down OTP / password-reset deliverability.

Env:
  RESEND_API_KEY          re_...  (enables Resend for both paths)
  RESEND_FROM             transactional From, e.g. "IELTS Computer Test <noreply@englishoncomputer.com>"
  RESEND_MARKETING_FROM   marketing From,      e.g. "IELTS Computer Test <news@englishoncomputer.com>"
"""
import os
import logging
import time
import uuid

import requests

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"

# Ported from the VN tree. A failed send is not cosmetic (it may be a verification code
# a student is waiting for), so transport errors are retried within a short deadline.
#
# Retrying an email is only safe with an idempotency key. A read timeout is ambiguous —
# Resend may well have accepted and sent the message before the answer was lost — so a
# blind retry would deliver the same code twice. The key makes Resend return the result
# of the original request instead of sending again, which is what lets us retry the
# ambiguous case at all.
_SEND_DEADLINE = 20.0                       # seconds of re-dialling before giving up
_SEND_TIMEOUT = (4, 15)                     # (connect, read)

DEFAULT_FROM = "IELTS Computer Test <noreply@englishoncomputer.com>"
DEFAULT_MARKETING_FROM = "IELTS Computer Test <news@englishoncomputer.com>"


def resend_configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY"))


def resend_marketing_enabled() -> bool:
    # Marketing must opt in explicitly: a big broadcast on Resend can be far
    # pricier than a bulk ESP / SES, so setting the auth key alone must NOT
    # route 30k-recipient blasts through Resend.
    return resend_configured() and os.getenv("RESEND_MARKETING", "").lower() in ("1", "true", "yes")


def transactional_from() -> str:
    return os.getenv("RESEND_FROM", DEFAULT_FROM)


def marketing_from() -> str:
    return os.getenv("RESEND_MARKETING_FROM", DEFAULT_MARKETING_FROM)


def send_via_resend(from_addr: str, to_email: str, subject: str, html: str,
                    attachments: list = None) -> bool:
    """Send one email through Resend. Returns True on success, False otherwise.

    `attachments` is an optional list of Resend attachment dicts, e.g. an inline
    image: {"filename": "logo.png", "content": "<base64>", "content_id": "brandlogo"}
    referenced from the HTML as <img src="cid:brandlogo">.
    """
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.error("send_via_resend called but RESEND_API_KEY is not set")
        return False
    payload = {
        "from": from_addr,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    if attachments:
        payload["attachments"] = attachments
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": str(uuid.uuid4()),
    }
    deadline = time.monotonic() + _SEND_DEADLINE
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = requests.post(
                RESEND_API_URL, headers=headers, json=payload, timeout=_SEND_TIMEOUT,
            )
        except requests.RequestException as e:
            if time.monotonic() < deadline:
                time.sleep(min(0.5 * attempt, 2.0))
                continue
            logger.error(f"Resend send to {to_email} errored after {attempt} tries: {e}")
            return False
        except Exception as e:                  # noqa: BLE001 — never let mail kill the caller
            logger.error(f"Resend send to {to_email} errored: {e}")
            return False

        if resp.status_code // 100 == 2:
            return True
        # 5xx is Resend having a bad moment; the key makes another go harmless.
        if resp.status_code // 100 == 5 and time.monotonic() < deadline:
            time.sleep(min(0.5 * attempt, 2.0))
            continue
        logger.error(f"Resend send to {to_email} failed: {resp.status_code} {resp.text[:300]}")
        return False
