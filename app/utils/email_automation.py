"""Automated lifecycle emails (2026-11-14, "chính sách email mới" — global version).

1. Welcome — a few minutes after an account is created.
2. Abandoned checkout — the user opened the payment page for a VIP package
   (`checkout_intents`) and no payment followed within ABANDON_DELAY_MIN.

A background loop in main.py calls `run_once()` every LOOP_SECONDS. Each email is sent at
most once: a `marketing_email_logs` row is inserted (unique user/kind/ref) BEFORE sending,
which also stops the two uvicorn workers from both sending it.

Env:
  EMAIL_AUTOMATION_ENABLED   1 (default) / 0
  WELCOME_DELAY_MIN          3   minutes after signup
  ABANDON_DELAY_MIN          60  minutes after opening the payment page
  ABANDON_COOLDOWN_DAYS      30  at most one reminder per user in this window
"""
import logging
import os
import re
from datetime import timedelta

from sqlalchemy import exists
from sqlalchemy.exc import IntegrityError

from app.models.models import CheckoutIntent, MarketingEmailLog, User, VIPSubscription
from app.utils.datetime_utils import get_vietnam_time
from app.utils.email_blocklist import is_disposable_email
from app.utils.email_suppression import suppressed_emails, unsubscribe_headers, with_unsubscribe_footer

logger = logging.getLogger(__name__)

ENABLED = os.getenv("EMAIL_AUTOMATION_ENABLED", "1") not in ("0", "false", "False", "")
LOOP_SECONDS = 120
WELCOME_DELAY_MIN = int(os.getenv("WELCOME_DELAY_MIN", "3"))
WELCOME_WINDOW_HOURS = 6          # never mail old accounts (e.g. right after the first deploy)
ABANDON_DELAY_MIN = int(os.getenv("ABANDON_DELAY_MIN", "60"))
ABANDON_WINDOW_HOURS = 48
ABANDON_COOLDOWN_DAYS = int(os.getenv("ABANDON_COOLDOWN_DAYS", "30"))
BATCH = 50
SITE_URL = os.getenv("PUBLIC_APP_URL", "https://englishoncomputer.com").rstrip("/")

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
PLACEHOLDER = {"noreply", "no-reply", "donotreply", "test", "testing", "example", "fake", "null", "none"}


def _now():
    return get_vietnam_time().replace(tzinfo=None)


# ── Email copy (as supplied in the brief) ───────────────────────────────────

def _wrap(inner: str) -> str:
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:600px;margin:0 auto;padding:24px;
color:#1f2937;font-size:15px;line-height:1.6">{inner}</div>"""


WELCOME_SUBJECT = "🔥 GET READY TO HIT THE IELTS PREDICTIONS!"


def welcome_html() -> str:
    return _wrap(f"""
<p>Hi there,</p>
<p>Welcome to <b>EnglishOnComputer!</b></p>
<p><b>DON'T JUST PRACTICE IELTS. PREPARE TO HIT THE ACTUAL EXAM! 🎯</b></p>
<p>🔥 <b>RARE, AUTHENTIC IELTS QUESTIONS</b> – Access carefully selected questions from the IELTS question bank.</p>
<p>🎯 <b>POWERFUL IELTS PREDICTIONS</b> – High-accuracy predictions covering ALL FOUR SKILLS: Listening, Reading, Writing and Speaking.</p>
<p>⭐ <b>FOUR-STAR TOPICS (⭐⭐⭐⭐)</b> – Your highest-priority predictions. Study these topics carefully. Don't leave them behind!</p>
<p>🚀 <b>YOUR VIP ADVANTAGE:</b> You're not just investing in practice materials. You're gaining access to exclusive predictions that could make a real difference on your exam day.</p>
<p><b>STUDY THE RIGHT TOPICS. RECOGNIZE THE QUESTIONS. HIT YOUR TARGET BAND!</b></p>
<p>Don't miss your chance to prepare smarter and maximize your chances of hitting the exam!</p>
<p><b>Your IELTS success starts here.</b></p>
<p><b>EnglishOnComputer Team</b><br><a href="{SITE_URL}" style="color:#0096b1">{SITE_URL}</a></p>
""")


ABANDON_SUBJECT = "🎯 Don't Miss Your IELTS Prediction Advantage!"


def abandoned_html() -> str:
    return _wrap(f"""
<p>Hi there,</p>
<p><b>Still hesitating about VIP? You could be missing your biggest chance to HIT THE IELTS EXAM!</b></p>
<p>VIP gives you MORE than practice. It unlocks our <b>EXCLUSIVE IELTS PREDICTION COLLECTION!</b></p>
<p>🔥 <b>RARE, AUTHENTIC IELTS QUESTIONS</b> – Carefully selected questions from the IELTS question bank.</p>
<p>🎯 <b>HIGH-ACCURACY PREDICTIONS</b> – Covering ALL FOUR SKILLS: Listening, Reading, Writing and Speaking.</p>
<p>⭐ <b>FOUR-STAR TOPICS (★★★★)</b> – Your highest-priority predictions. Study them first. Don't miss them!</p>
<p><b>STOP PRACTICING RANDOMLY. START PREPARING FOR TOPICS THAT COULD ACTUALLY APPEAR!</b></p>
<p>Imagine walking into your exam and recognizing a topic you've already mastered!</p>
<p>🚀 <b>UPGRADE TO VIP. PREPARE SMARTER. MAXIMIZE YOUR CHANCE OF HITTING YOUR TARGET BAND!</b></p>
<p style="margin:24px 0"><a href="{SITE_URL}/vip-packages" style="background:#eb7e37;color:#fff;text-decoration:none;
padding:12px 22px;border-radius:8px;font-weight:bold;display:inline-block">Upgrade to VIP</a></p>
<p><b>Don't miss your opportunity!</b></p>
<p>EnglishOnComputer Team</p>
""")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _deliverable(email: str, suppressed: set) -> bool:
    e = (email or "").strip().lower()
    if not EMAIL_RE.match(e) or e in suppressed or is_disposable_email(e):
        return False
    return e.split("@", 1)[0].split("+", 1)[0] not in PLACEHOLDER


def _claim(db, user: User, kind: str, ref: str = "") -> MarketingEmailLog:
    row = MarketingEmailLog(user_id=user.user_id, kind=kind, ref=ref, email=user.email,
                            status="sending", created_at=_now())
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return row


def _send(db, row: MarketingEmailLog, subject: str, html: str):
    from app.utils.email_service import send_single_email
    ok = False
    try:
        ok = send_single_email(row.email, subject, with_unsubscribe_footer(html, row.email),
                               headers=unsubscribe_headers(row.email))
    except Exception as e:   # never let one address stop the loop
        logger.warning(f"lifecycle email to {row.email} failed: {e}")
    row.status = "sent" if ok else "failed"
    db.commit()


def _has_active_vip(db, user_id: int, now) -> bool:
    return db.query(exists().where(
        VIPSubscription.user_id == user_id,
        VIPSubscription.payment_status == "completed",
        VIPSubscription.end_date > now,
    )).scalar()


# ── Jobs ────────────────────────────────────────────────────────────────────

def send_welcome_emails(db) -> int:
    now = _now()
    already = exists().where(MarketingEmailLog.user_id == User.user_id, MarketingEmailLog.kind == "welcome")
    users = (db.query(User)
             .filter(User.created_at <= now - timedelta(minutes=WELCOME_DELAY_MIN),
                     User.created_at >= now - timedelta(hours=WELCOME_WINDOW_HOURS),
                     User.role.in_(("customer", "student")),
                     User.email.isnot(None), ~already)
             .order_by(User.created_at.asc()).limit(BATCH).all())
    if not users:
        return 0
    suppressed = suppressed_emails(db)
    sent = 0
    for u in users:
        row = _claim(db, u, "welcome")
        if row is None:
            continue
        if not _deliverable(u.email, suppressed):
            row.status = "skipped"
            db.commit()
            continue
        _send(db, row, WELCOME_SUBJECT, welcome_html())
        sent += 1
    return sent


def send_abandoned_checkout_emails(db) -> int:
    now = _now()
    intents = (db.query(CheckoutIntent)
               .filter(CheckoutIntent.created_at <= now - timedelta(minutes=ABANDON_DELAY_MIN),
                       CheckoutIntent.created_at >= now - timedelta(hours=ABANDON_WINDOW_HOURS))
               .order_by(CheckoutIntent.created_at.desc()).limit(BATCH * 4).all())
    if not intents:
        return 0
    latest = {}
    for it in intents:                      # one candidate per user: their latest visit
        latest.setdefault(it.user_id, it)
    suppressed = suppressed_emails(db)
    sent = 0
    for uid, it in list(latest.items())[:BATCH]:
        recent = db.query(exists().where(
            MarketingEmailLog.user_id == uid, MarketingEmailLog.kind == "abandoned_checkout",
            MarketingEmailLog.created_at >= now - timedelta(days=ABANDON_COOLDOWN_DAYS))).scalar()
        if recent:
            continue
        paid_since = db.query(exists().where(
            VIPSubscription.user_id == uid, VIPSubscription.payment_status == "completed",
            VIPSubscription.created_at >= it.created_at - timedelta(minutes=5))).scalar()
        if paid_since or _has_active_vip(db, uid, now):
            continue
        u = db.query(User).filter(User.user_id == uid).first()
        if not u or not u.email:
            continue
        row = _claim(db, u, "abandoned_checkout", str(it.id))
        if row is None:
            continue
        if not _deliverable(u.email, suppressed):
            row.status = "skipped"
            db.commit()
            continue
        _send(db, row, ABANDON_SUBJECT, abandoned_html())
        sent += 1
    return sent


def run_once() -> dict:
    """One pass of both jobs. Sync (DB + HTTP): main.py runs it in a worker thread."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return {"welcome": send_welcome_emails(db), "abandoned": send_abandoned_checkout_emails(db)}
    except Exception as e:
        db.rollback()
        logger.warning(f"email automation pass failed: {e}")
        return {}
    finally:
        db.close()


async def automation_loop():
    import asyncio
    if not ENABLED:
        logger.info("Email automation disabled (EMAIL_AUTOMATION_ENABLED=0)")
        return
    await asyncio.sleep(30)                 # let startup finish first
    while True:
        try:
            res = await asyncio.to_thread(run_once)
            if res and any(res.values()):
                logger.info(f"email automation: {res}")
        except Exception as e:
            logger.warning(f"email automation loop error: {e}")
        await asyncio.sleep(LOOP_SECONDS)
