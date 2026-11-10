"""Admin side of "Work With Us" (UGC creator program).

/admin/creator-program/applications        list / update status + note
/admin/creator-program/videos              CRUD of the video cards
/admin/creator-program/videos/fetch-meta   auto thumbnail (+ handle) from a video link
/admin/creator-program/creators            CRUD of the creator cards
/admin/creator-program/settings            countries offered in the form, headline figure
"""
import re
from typing import List, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import (
    User, CreatorApplication, CreatorShowcaseVideo, CreatorTestimonial, SystemSetting, CreatorProfile,
)
from app.routes.admin.auth import get_current_admin
from app.routes.customer.creator_program import (
    COUNTRIES_KEY, TOTAL_EARNED_KEY, country_codes, get_setting,
)
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()
P = "/creator-program"
STATUSES = ("new", "contacted", "approved", "rejected")


def _now():
    return get_vietnam_time().replace(tzinfo=None)


# ── Applications ──────────────────────────────────────────────────────────────

@router.get(P + "/applications")
async def list_applications(
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    query = db.query(CreatorApplication)
    if status in STATUSES:
        query = query.filter(CreatorApplication.status == status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            CreatorApplication.full_name.ilike(like) | CreatorApplication.email.ilike(like)
            | CreatorApplication.phone.ilike(like))
    total = query.count()
    rows = (query.order_by(CreatorApplication.created_at.desc(), CreatorApplication.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    counts = {s: db.query(CreatorApplication).filter(CreatorApplication.status == s).count() for s in STATUSES}
    # Linked account (by user_id, else a verified account with the same email) + its Hub access.
    uids = {a.user_id for a in rows if a.user_id}
    emails = {(a.email or "").strip().lower() for a in rows if not a.user_id and a.email}
    users = {u.user_id: u for u in db.query(User).filter(User.user_id.in_(uids)).all()} if uids else {}
    by_email = {}
    if emails:
        for u in db.query(User).filter(func.lower(User.email).in_(emails), User.email_verified == True).all():  # noqa: E712
            by_email[(u.email or "").strip().lower()] = u
    access = {p.user_id: bool(p.hub_access) for p in db.query(CreatorProfile).filter(
        CreatorProfile.user_id.in_(set(users) | {u.user_id for u in by_email.values()})).all()} if (users or by_email) else {}

    def _acct(a):
        u = users.get(a.user_id) if a.user_id else by_email.get((a.email or "").strip().lower())
        if not u:
            return None
        return {"user_id": u.user_id, "username": u.username, "email": u.email,
                "linked": bool(a.user_id), "hub_access": access.get(u.user_id, False)}

    return {
        "total": total, "page": page, "page_size": page_size, "counts": counts,
        "items": [{
            "id": a.id, "user_id": a.user_id, "full_name": a.full_name,
            "country_code": a.country_code, "languages": a.languages, "phone": a.phone,
            "email": a.email, "status": a.status, "admin_note": a.admin_note,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "reject_reason": a.reject_reason, "source": a.source or "landing", "country_other": a.country_other,
            "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
            "account": _acct(a),
        } for a in rows],
    }


class ApplicationUpdate(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = None


@router.put(P + "/applications/{app_id}")
async def update_application(app_id: int, payload: ApplicationUpdate, db: Session = Depends(get_db),
                             current_admin: User = Depends(get_current_admin)):
    a = db.query(CreatorApplication).filter(CreatorApplication.id == app_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")
    if payload.status is not None:
        if payload.status not in STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        # Approve / reject have side effects (Creator Hub access, reason) — use their own endpoints.
        if payload.status in ("approved", "rejected") and payload.status != a.status:
            raise HTTPException(status_code=400, detail="Dùng nút Duyệt / Từ chối để đổi sang trạng thái này.")
        a.status = payload.status
    if payload.admin_note is not None:
        a.admin_note = payload.admin_note
    a.updated_at = _now()
    db.commit()
    return {"ok": True}


@router.delete(P + "/applications/{app_id}")
async def delete_application(app_id: int, db: Session = Depends(get_db),
                             current_admin: User = Depends(get_current_admin)):
    a = db.query(CreatorApplication).filter(CreatorApplication.id == app_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")
    db.delete(a)
    db.commit()
    return {"ok": True}


# ── Video cards ───────────────────────────────────────────────────────────────

def detect_platform(url: str) -> str:
    u = (url or "").lower()
    if "tiktok.com" in u:
        return "tiktok"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "instagram.com" in u:
        return "instagram"
    if "facebook.com" in u or "fb.watch" in u:
        return "facebook"
    return "other"


def _youtube_id(url: str) -> Optional[str]:
    m = re.search(r"(?:youtu\.be/|v=|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def _store_remote_image(img_url: str) -> Optional[str]:
    """Copy a remote image to R2 (TikTok thumbnail URLs are signed and expire)."""
    try:
        r = httpx.get(img_url, timeout=15, follow_redirects=True)
        ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
        if r.status_code != 200 or not ctype.startswith("image/") or len(r.content) > 5_000_000:
            return None
        from app.utils.r2_storage import upload_image_to_r2
        ext = {"image/png": ".png", "image/webp": ".webp"}.get(ctype, ".jpg")
        return upload_image_to_r2(r.content, f"creator_{uuid4()}{ext}", content_type=ctype)
    except Exception:
        return None


class FetchMetaIn(BaseModel):
    video_url: str


@router.post(P + "/videos/fetch-meta")
async def fetch_video_meta(payload: FetchMetaIn, current_admin: User = Depends(get_current_admin)):
    """Best effort: thumbnail + @handle for YouTube / TikTok links. Instagram and
    Facebook need an app token for oEmbed, so their thumbnails are uploaded by hand.
    View / like counts are not exposed by these public endpoints."""
    url = payload.video_url.strip()
    platform = detect_platform(url)
    out = {"platform": platform, "thumbnail_url": None, "handle": None}
    if platform == "youtube":
        vid = _youtube_id(url)
        if vid:
            out["thumbnail_url"] = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        try:
            r = httpx.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"}, timeout=10)
            if r.status_code == 200:
                author_url = r.json().get("author_url") or ""
                m = re.search(r"/(@[^/?#]+)", author_url)
                out["handle"] = m.group(1) if m else (r.json().get("author_name") or None)
        except Exception:
            pass
    elif platform == "tiktok":
        try:
            r = httpx.get("https://www.tiktok.com/oembed", params={"url": url}, timeout=10,
                          follow_redirects=True)
            if r.status_code == 200:
                data = r.json()
                if data.get("author_unique_id"):
                    out["handle"] = "@" + data["author_unique_id"]
                if data.get("thumbnail_url"):
                    out["thumbnail_url"] = _store_remote_image(data["thumbnail_url"]) or data["thumbnail_url"]
        except Exception:
            pass
    if not out["thumbnail_url"]:
        out["message"] = "Could not fetch a thumbnail automatically for this link. Please upload one."
    return out


class VideoIn(BaseModel):
    video_url: str
    thumbnail_url: Optional[str] = None
    handle: Optional[str] = None
    likes: Optional[str] = None
    views: Optional[str] = None
    earned: Optional[str] = None
    display_order: Optional[int] = 0
    is_active: Optional[bool] = True


def _video_dict(v: CreatorShowcaseVideo):
    return {
        "id": v.id, "video_url": v.video_url, "platform": v.platform, "thumbnail_url": v.thumbnail_url,
        "handle": v.handle, "likes": v.likes, "views": v.views, "earned": v.earned,
        "display_order": v.display_order or 0, "is_active": bool(v.is_active),
    }


@router.get(P + "/videos")
async def list_videos(db: Session = Depends(get_db), current_admin: User = Depends(get_current_admin)):
    rows = db.query(CreatorShowcaseVideo).order_by(CreatorShowcaseVideo.display_order.asc(),
                                                   CreatorShowcaseVideo.id.desc()).all()
    return [_video_dict(v) for v in rows]


@router.post(P + "/videos")
async def create_video(payload: VideoIn, db: Session = Depends(get_db),
                       current_admin: User = Depends(get_current_admin)):
    if not payload.video_url.strip():
        raise HTTPException(status_code=400, detail="Video link is required")
    v = CreatorShowcaseVideo(**payload.dict(), platform=detect_platform(payload.video_url), created_at=_now())
    v.video_url = v.video_url.strip()
    db.add(v)
    db.commit()
    db.refresh(v)
    return _video_dict(v)


@router.put(P + "/videos/{video_id}")
async def update_video(video_id: int, payload: VideoIn, db: Session = Depends(get_db),
                       current_admin: User = Depends(get_current_admin)):
    v = db.query(CreatorShowcaseVideo).filter(CreatorShowcaseVideo.id == video_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Video not found")
    for k, val in payload.dict().items():
        setattr(v, k, val)
    v.video_url = (v.video_url or "").strip()
    v.platform = detect_platform(v.video_url)
    db.commit()
    return _video_dict(v)


@router.delete(P + "/videos/{video_id}")
async def delete_video(video_id: int, db: Session = Depends(get_db),
                       current_admin: User = Depends(get_current_admin)):
    v = db.query(CreatorShowcaseVideo).filter(CreatorShowcaseVideo.id == video_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Video not found")
    db.delete(v)
    db.commit()
    return {"ok": True}


# ── Creator cards ─────────────────────────────────────────────────────────────

class CreatorIn(BaseModel):
    name: str
    avatar_url: Optional[str] = None
    subtitle: Optional[str] = None
    earned: Optional[str] = None
    note: Optional[str] = None
    profile_url: Optional[str] = None
    display_order: Optional[int] = 0
    is_active: Optional[bool] = True


def _creator_dict(c: CreatorTestimonial):
    return {
        "id": c.id, "name": c.name, "avatar_url": c.avatar_url, "subtitle": c.subtitle,
        "earned": c.earned, "note": c.note, "profile_url": c.profile_url,
        "display_order": c.display_order or 0, "is_active": bool(c.is_active),
    }


@router.get(P + "/creators")
async def list_creators(db: Session = Depends(get_db), current_admin: User = Depends(get_current_admin)):
    rows = db.query(CreatorTestimonial).order_by(CreatorTestimonial.display_order.asc(),
                                                 CreatorTestimonial.id.desc()).all()
    return [_creator_dict(c) for c in rows]


@router.post(P + "/creators")
async def create_creator(payload: CreatorIn, db: Session = Depends(get_db),
                         current_admin: User = Depends(get_current_admin)):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    c = CreatorTestimonial(**payload.dict(), created_at=_now())
    db.add(c)
    db.commit()
    db.refresh(c)
    return _creator_dict(c)


@router.put(P + "/creators/{creator_id}")
async def update_creator(creator_id: int, payload: CreatorIn, db: Session = Depends(get_db),
                         current_admin: User = Depends(get_current_admin)):
    c = db.query(CreatorTestimonial).filter(CreatorTestimonial.id == creator_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Creator not found")
    for k, val in payload.dict().items():
        setattr(c, k, val)
    db.commit()
    return _creator_dict(c)


@router.delete(P + "/creators/{creator_id}")
async def delete_creator(creator_id: int, db: Session = Depends(get_db),
                         current_admin: User = Depends(get_current_admin)):
    c = db.query(CreatorTestimonial).filter(CreatorTestimonial.id == creator_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Creator not found")
    db.delete(c)
    db.commit()
    return {"ok": True}


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get(P + "/settings")
async def get_settings(db: Session = Depends(get_db), current_admin: User = Depends(get_current_admin)):
    return {"countries": country_codes(db), "total_earned": get_setting(db, TOTAL_EARNED_KEY, "") or ""}


class SettingsIn(BaseModel):
    countries: List[str]
    total_earned: Optional[str] = ""


def _put_setting(db, key, value):
    row = db.query(SystemSetting).filter(SystemSetting.setting_key == key).first()
    if row:
        row.setting_value = value
        row.updated_at = _now()
    else:
        db.add(SystemSetting(setting_key=key, setting_value=value, updated_at=_now()))


@router.put(P + "/settings")
async def save_settings(payload: SettingsIn, db: Session = Depends(get_db),
                        current_admin: User = Depends(get_current_admin)):
    codes = []
    for c in payload.countries:
        c = (c or "").strip().upper()
        if re.fullmatch(r"[A-Z]{2}", c) and c not in codes:
            codes.append(c)
    value = ",".join(codes)
    if len(value) > 255:
        raise HTTPException(status_code=400, detail="Too many countries (max about 80).")
    _put_setting(db, COUNTRIES_KEY, value)
    _put_setting(db, TOTAL_EARNED_KEY, (payload.total_earned or "").strip()[:40])
    db.commit()
    return {"ok": True, "countries": codes}
