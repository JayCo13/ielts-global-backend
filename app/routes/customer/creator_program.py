"""Public "Work With Us" (UGC creator program) endpoints — no login required.

GET  /creator-program        page data: countries to offer, showcase videos, creator cards
POST /creator-program/apply  multi-step sign-up form (name, country, languages, phone, email)

Anti-spam on /apply (2026-11):
  * honeypot field + per-IP throttle (as before)
  * signed form token from GET /creator-program: must be >= FORM_MIN_SECONDS old (bots post instantly
    or skip the page) and <= FORM_MAX_AGE old
  * Cloudflare Turnstile when TURNSTILE_SECRET_KEY / TURNSTILE_SITE_KEY are set (no-op otherwise)
  * one open application per person: same account, email or phone with a pending/approved
    application -> 409 "already applied"
"""
import hashlib
import hmac
import logging
import os
import re
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import (
    CreatorApplication, CreatorShowcaseVideo, CreatorTestimonial, SystemSetting,
)
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()
logger = logging.getLogger(__name__)

FORM_MIN_SECONDS = 4
FORM_MAX_AGE = 6 * 3600
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _turnstile_keys():
    return os.getenv("TURNSTILE_SITE_KEY", "").strip(), os.getenv("TURNSTILE_SECRET_KEY", "").strip()


def _form_sig(ts: str) -> str:
    from app.routes.admin.auth import SECRET_KEY
    return hmac.new(f"creator-form:{SECRET_KEY}".encode(), ts.encode(), hashlib.sha256).hexdigest()[:32]


def make_form_token() -> str:
    ts = str(int(time.time()))
    return f"{ts}.{_form_sig(ts)}"


def check_form_token(token: Optional[str]) -> bool:
    try:
        ts, sig = (token or "").split(".", 1)
        age = time.time() - int(ts)
    except ValueError:
        return False
    return hmac.compare_digest(sig, _form_sig(ts)) and FORM_MIN_SECONDS <= age <= FORM_MAX_AGE


async def verify_turnstile(token: Optional[str], ip: str) -> bool:
    _, secret = _turnstile_keys()
    if not secret:
        return True                      # captcha not configured -> other layers only
    if not token:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(TURNSTILE_VERIFY_URL, data={"secret": secret, "response": token, "remoteip": ip})
            return bool(r.json().get("success"))
    except Exception as exc:             # Cloudflare unreachable: don't lock real people out
        logger.warning("turnstile verify failed: %s", exc)
        return True


_OPEN_APP_STATUSES = ("new", "contacted", "approved")


def _digits(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")


def find_existing_application(db, user_id: Optional[int], email: Optional[str], phone: str):
    """A pending/approved application from the same account, email or phone number (rejected ones
    don't count, so a rejected person can apply again)."""
    from sqlalchemy import func, or_
    q = db.query(CreatorApplication).filter(CreatorApplication.status.in_(_OPEN_APP_STATUSES))
    conds = []
    if user_id:
        conds.append(CreatorApplication.user_id == user_id)
    if email:
        conds.append(func.lower(CreatorApplication.email) == email.lower())
    if conds:
        hit = q.filter(or_(*conds)).first()
        if hit:
            return hit
    want = _digits(phone)
    if len(want) >= 6:
        tail = want[-4:]
        for a in q.filter(CreatorApplication.phone.isnot(None)).with_entities(CreatorApplication.id, CreatorApplication.phone).all():
            d = _digits(a.phone)
            if d.endswith(tail) and d == want:
                return db.query(CreatorApplication).get(a.id)
    return None

# system_settings keys (admin-editable in the "Work With Us" admin page)
COUNTRIES_KEY = "creator_countries"        # comma-separated ISO alpha-2 codes, in display order
TOTAL_EARNED_KEY = "creator_total_earned"  # headline figure, e.g. "$20,000"; empty = line hidden
DEFAULT_COUNTRIES = "CA,EG,DE,ID,RU,SY,TN,AE,US,KZ,UZ,TR,SA"
LANGUAGES = ["Russian", "Kazakh", "Uzbek", "English", "Indonesian", "Turkish", "Arabic", "Other"]


def get_setting(db, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.query(SystemSetting).filter(SystemSetting.setting_key == key).first()
    return row.setting_value if row and row.setting_value is not None else default


def country_codes(db) -> List[str]:
    raw = get_setting(db, COUNTRIES_KEY, DEFAULT_COUNTRIES) or ""
    return [c.strip().upper() for c in raw.split(",") if c.strip()]


@router.get("/creator-program")
async def creator_program_page(db: Session = Depends(get_db)):
    videos = (db.query(CreatorShowcaseVideo).filter(CreatorShowcaseVideo.is_active == True)  # noqa: E712
              .order_by(CreatorShowcaseVideo.display_order.asc(), CreatorShowcaseVideo.id.desc()).all())
    creators = (db.query(CreatorTestimonial).filter(CreatorTestimonial.is_active == True)  # noqa: E712
                .order_by(CreatorTestimonial.display_order.asc(), CreatorTestimonial.id.desc()).all())
    return {
        "countries": country_codes(db),
        "form_token": make_form_token(),
        "captcha_site_key": _turnstile_keys()[0] or None,
        "languages": LANGUAGES,
        "total_earned": (get_setting(db, TOTAL_EARNED_KEY, "") or "").strip(),
        "videos": [{
            "id": v.id, "video_url": v.video_url, "platform": v.platform,
            "thumbnail_url": v.thumbnail_url, "handle": v.handle,
            "likes": v.likes, "views": v.views, "earned": v.earned,
        } for v in videos],
        "creators": [{
            "id": c.id, "name": c.name, "avatar_url": c.avatar_url, "subtitle": c.subtitle,
            "earned": c.earned, "note": c.note, "profile_url": c.profile_url,
        } for c in creators],
    }


class ApplyIn(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=120)
    country_code: str = Field(..., min_length=2, max_length=8)
    languages: List[str] = Field(default_factory=list)
    phone: str = Field(..., min_length=4, max_length=40)
    email: Optional[str] = Field(None, max_length=255)
    country_other: Optional[str] = Field(None, max_length=100)   # name picked/typed under "Other country"
    form_token: Optional[str] = Field(None, max_length=80)
    captcha_token: Optional[str] = Field(None, max_length=4096)
    website: Optional[str] = None   # honeypot — real visitors never fill it


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9 ()\-.]{4,40}$")
_recent = {}   # ip -> [timestamps]; light in-memory throttle (per worker)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _optional_user_id(request: Request, db) -> Optional[int]:
    """Attach the application to the logged-in account when a valid student token is sent."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        from jose import jwt
        from app.routes.admin.auth import SECRET_KEY, ALGORITHM
        from app.models.models import User
        username = jwt.decode(auth[7:], SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
        if not username:
            return None
        u = db.query(User.user_id).filter(User.username == username).first()
        return u[0] if u else None
    except Exception:
        return None


@router.post("/creator-program/apply")
async def apply(payload: ApplyIn, request: Request, db: Session = Depends(get_db)):
    if payload.website:
        return {"ok": True}
    ip = _client_ip(request)
    now = time.time()
    hits = [t for t in _recent.get(ip, []) if now - t < 3600]
    if len(hits) >= 5:
        raise HTTPException(status_code=429, detail="Too many applications from this connection. Please try again later.")
    hits.append(now)
    _recent[ip] = hits

    name = payload.full_name.strip()
    phone = re.sub(r"\s+", " ", re.sub(r"[-\u2010-\u2015]", "", payload.phone)).strip()   # WhatsApp number, no dashes
    email = (payload.email or "").strip() or None
    country = payload.country_code.strip().upper()
    if not name:
        raise HTTPException(status_code=400, detail="Please enter your name.")
    if not _PHONE_RE.match(phone) or len(re.sub(r"\D", "", phone)) < 6:
        raise HTTPException(status_code=400, detail="Please enter a valid phone number.")
    if email and not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if not check_form_token(payload.form_token):
        raise HTTPException(status_code=400, detail="This form has expired. Please reload the page and try again.")
    if not await verify_turnstile(payload.captcha_token, ip):
        raise HTTPException(status_code=400, detail="Please complete the security check and try again.")
    user_id = _optional_user_id(request, db)
    dup = find_existing_application(db, user_id, email, phone)
    if dup:
        raise HTTPException(status_code=409, detail={
            "code": "already_applied", "status": "approved" if dup.status == "approved" else "pending",
            "message": ("You're already part of our Creator Program." if dup.status == "approved" else
                        "You've already applied. Our manager will contact you soon."),
        })
    if country != "OTHER" and country not in country_codes(db):
        country = "OTHER"
    other = None
    if country == "OTHER":
        other = re.sub(r"\s+", " ", (payload.country_other or "")).strip()[:100] or None
    langs = [l.strip()[:30] for l in payload.languages if l and l.strip()][:15]

    now_vn = get_vietnam_time().replace(tzinfo=None)
    db.add(CreatorApplication(
        user_id=user_id,
        full_name=name, country_code=country, country_other=other, languages=", ".join(langs)[:255],
        phone=phone, email=email, status="new", ip_address=ip,
        created_at=now_vn, updated_at=now_vn,
    ))
    db.commit()
    return {"ok": True}
