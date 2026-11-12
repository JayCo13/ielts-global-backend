"""Creator Hub — student side (Profile → Creator Hub), 2026-11.

GET  /creator-hub/status                 access flag + latest application (+ auto-link by verified email)
POST /creator-hub/apply                  Creator Application (full name, email, WhatsApp)
PUT  /creator-hub/profile                complete personal details (access required)
GET  /creator-hub/channels               my channels + rates + open reconciliation
POST /creator-hub/channels               register a social channel (-> pending)
PUT  /creator-hub/channels/primary       choose the primary channel for the current period
GET  /creator-hub/reconciliations        my submissions (with per-channel lines)
POST /creator-hub/reconciliations        submit views for approved channels (>= SUBMIT_MIN_DAYS apart)
POST /creator-hub/proofs                 upload a proof of views (image / short video) -> URL
GET  /creator-hub/earnings               creator earnings summary (wallet is the affiliate wallet)
GET  /creator-hub/leaderboard            Creator Leaderboard (lifetime | monthly)

Everything except /status and /apply requires Creator Hub Access (see app/utils/creator_hub.py).
"""
import json
import os
import re
import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import (
    User, CreatorApplication, CreatorChannel, CreatorReconciliation, CreatorReconciliationItem,
    AffiliateWalletTx, CreatorChannelLog,
)
from app.routes.admin.auth import get_current_student
from app.routes.customer.creator_program import _EMAIL_RE, _PHONE_RE
from app.utils.affiliate import AFFILIATE_CURRENCY, UNIT_DIVISOR
from app.utils.creator_hub import (
    PLATFORMS, PRIMARY_RATE_PER_K, SECONDARY_RATE_PER_K, OPEN_RECON_STATUSES, SUBMIT_MIN_DAYS,
    now, app_status_label, get_profile, has_hub_access, latest_application, set_hub_access,
    normalize_channel_url, detect_platform, reward_usd, next_submit_at, platform_label,
)
from app.utils.creator_leaderboard import abbreviate_name, build_leaderboard, available_months

router = APIRouter(prefix="/creator-hub")


def require_hub(db: Session = Depends(get_db), current: User = Depends(get_current_student)) -> User:
    if not has_hub_access(db, current.user_id):
        raise HTTPException(status_code=403, detail="Creator Hub is not enabled for this account.")
    return current


def affiliate_gate(db: Session = Depends(get_db), current: User = Depends(get_current_student)):
    """Affiliate Program sits inside Creator Hub: link, withdrawals and payout details
    need Hub access. Referral clicks / sign-up attribution also only count for owners
    with Hub access (utils.creator_hub.referral_allowed). Set CREATOR_HUB_GATE=0
    to switch both checks off (old behaviour)."""
    if os.getenv("CREATOR_HUB_GATE", "1") == "0":
        return
    if not has_hub_access(db, current.user_id):
        raise HTTPException(status_code=403, detail="Creator Hub is not enabled for this account.")


def _iso(d):
    return d.isoformat() if d else None


def _app_dict(a: Optional[CreatorApplication]):
    if not a:
        return None
    return {
        "id": a.id, "status": app_status_label(a.status), "full_name": a.full_name,
        "email": a.email, "phone": a.phone, "reject_reason": a.reject_reason,
        "submitted_at": _iso(a.created_at), "reviewed_at": _iso(a.reviewed_at),
    }


def _auto_link(db, user: User):
    """Attach applications sent from the public /work-with-us form while logged out,
    matched by the account's VERIFIED email. An already-approved one enables access."""
    if not user.email or not getattr(user, "email_verified", False):
        return
    rows = (db.query(CreatorApplication)
            .filter(CreatorApplication.user_id.is_(None),
                    func.lower(CreatorApplication.email) == user.email.strip().lower())
            .all())
    if not rows:
        return
    for a in rows:
        a.user_id = user.user_id
    approved = [a for a in rows if a.status == "approved"]
    if approved and not has_hub_access(db, user.user_id):
        set_hub_access(db, user, True, "application", application=approved[-1],
                       note=f"Approved application #{approved[-1].id} (matched by email)")
    db.commit()


@router.get("/status")
async def status(db: Session = Depends(get_db), current: User = Depends(get_current_student)):
    _auto_link(db, current)
    p = get_profile(db, current.user_id)
    a = latest_application(db, current)
    enabled = bool(p and p.hub_access)
    return {
        "hub_access": enabled,
        "application": _app_dict(a),
        "can_apply": (not enabled) and (a is None or app_status_label(a.status) == "rejected"),
        "profile": ({
            "full_name": p.full_name, "phone": p.phone, "email": p.email, "country_code": p.country_code,
            "complete": bool(p.full_name and p.phone),
        } if p and enabled else None),
        "defaults": {"email": current.email, "username": current.username},
    }


class HubApplyIn(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., max_length=255)
    phone: str = Field(..., min_length=4, max_length=40)


def _clean_contact(full_name: str, email: str, phone: str):
    name, email = full_name.strip(), (email or "").strip()
    phone = re.sub(r"\s+", " ", re.sub(r"[-\u2010-\u2015]", "", phone or "")).strip()   # WhatsApp number, no dashes
    if not name:
        raise HTTPException(status_code=400, detail="Please enter your full name.")
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if not _PHONE_RE.match(phone) or len([c for c in phone if c.isdigit()]) < 6:
        raise HTTPException(status_code=400, detail="Please enter a valid WhatsApp number.")
    return name, email, phone


@router.post("/apply")
async def apply(payload: HubApplyIn, db: Session = Depends(get_db), current: User = Depends(get_current_student)):
    if has_hub_access(db, current.user_id):
        raise HTTPException(status_code=400, detail="Creator Hub is already enabled for your account.")
    last = latest_application(db, current)
    if last and app_status_label(last.status) == "pending":
        raise HTTPException(status_code=400, detail="Your application is already pending approval.")
    name, email, phone = _clean_contact(payload.full_name, payload.email, payload.phone)
    ts = now()
    db.add(CreatorApplication(user_id=current.user_id, full_name=name, email=email, phone=phone,
                              status="new", source="hub", created_at=ts, updated_at=ts))
    db.commit()
    return {"ok": True}


class ProfileIn(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., max_length=255)
    phone: str = Field(..., min_length=4, max_length=40)


@router.put("/profile")
async def update_profile(payload: ProfileIn, db: Session = Depends(get_db), current: User = Depends(require_hub)):
    name, email, phone = _clean_contact(payload.full_name, payload.email, payload.phone)
    p = get_profile(db, current.user_id)
    p.full_name, p.email, p.phone, p.updated_at = name, email, phone, now()
    db.commit()
    return {"ok": True}


# ── Channels ─────────────────────────────────────────────────────────────────

def _channel_dict(c: CreatorChannel, open_items=None):
    return {
        "id": c.id, "platform": c.platform, "platform_label": platform_label(c),
        "platform_name": c.platform_name, "initial_views": int(c.initial_views or 0),
        "channel_name": c.channel_name, "channel_url": c.channel_url, "status": c.status,
        "status_reason": c.status_reason, "is_primary": bool(c.is_primary),
        "approved_total_views": int(c.approved_total_views or 0),
        "last_submitted_total": int(c.last_submitted_total) if c.last_submitted_total is not None else None,
        "last_submitted_at": _iso(c.last_submitted_at),
        "registered_at": _iso(c.created_at),
        "open_item": open_items.get(c.id) if open_items else None,
    }


def _open_recon(db, user_id):
    return (db.query(CreatorReconciliation)
            .filter(CreatorReconciliation.user_id == user_id,
                    CreatorReconciliation.status.in_(OPEN_RECON_STATUSES))
            .order_by(CreatorReconciliation.id.desc()).first())


@router.get("/channels")
async def list_channels(db: Session = Depends(get_db), current: User = Depends(require_hub)):
    rows = (db.query(CreatorChannel).filter(CreatorChannel.user_id == current.user_id)
            .order_by(CreatorChannel.created_at.asc(), CreatorChannel.id.asc()).all())
    open_r = _open_recon(db, current.user_id)
    open_items = {}
    if open_r:
        for it in db.query(CreatorReconciliationItem).filter(
                CreatorReconciliationItem.reconciliation_id == open_r.id).all():
            open_items[it.channel_id] = {"submitted_total": int(it.submitted_total),
                                         "period_views": int(it.period_views),
                                         "channel_type": it.channel_type,
                                         "estimated_usd": it.estimated_usd}
    return {
        "channels": [_channel_dict(c, open_items) for c in rows],
        "platforms": [{"key": k, "label": v} for k, v in PLATFORMS.items()],
        "rates": {"primary_per_k": PRIMARY_RATE_PER_K, "secondary_per_k": SECONDARY_RATE_PER_K},
        "open_reconciliation": ({"id": open_r.id, "status": open_r.status,
                                 "submitted_at": _iso(open_r.submitted_at),
                                 "estimated_usd": open_r.estimated_usd} if open_r else None),
        "min_days_between": SUBMIT_MIN_DAYS,
        "next_submit_at": _iso(next_submit_at(db, current.user_id)),
    }


class ChannelIn(BaseModel):
    platform: Optional[str] = None
    platform_name: Optional[str] = Field(None, max_length=100)   # required when platform == 'other'
    channel_name: str = Field(..., min_length=1, max_length=255)
    channel_url: str = Field(..., min_length=4, max_length=500)
    initial_views: Optional[int] = Field(0, ge=0, le=10**12)     # blank / missing -> 0


def _clean_channel(db, payload: ChannelIn, user_id: int, exclude_id: Optional[int] = None):
    """Validate a channel form; returns (url, platform, platform_name, key, initial_views)."""
    url = payload.channel_url.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    platform = (payload.platform or "").strip().lower() or detect_platform(url)
    if platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail="Please choose the platform of this channel.")
    pname = None
    if platform == "other":
        pname = (payload.platform_name or "").strip()
        if not pname:
            raise HTTPException(status_code=400, detail="Please enter the platform name.")
    key = normalize_channel_url(url)
    q = (db.query(CreatorChannel)
         .filter(CreatorChannel.user_id == user_id, CreatorChannel.url_key == key,
                 CreatorChannel.status != "rejected"))
    if exclude_id:
        q = q.filter(CreatorChannel.id != exclude_id)
    if q.first():
        raise HTTPException(status_code=400, detail="You have already registered this channel.")
    return url, platform, pname, key, int(payload.initial_views or 0)


@router.post("/channels")
async def register_channel(payload: ChannelIn, db: Session = Depends(get_db), current: User = Depends(require_hub)):
    url, platform, pname, key, initial = _clean_channel(db, payload, current.user_id)
    if db.query(func.count(CreatorChannel.id)).filter(CreatorChannel.user_id == current.user_id).scalar() >= 30:
        raise HTTPException(status_code=400, detail="Too many channels registered.")
    ts = now()
    # Initial views = the channel's starting baseline: the first period counts views above it.
    c = CreatorChannel(user_id=current.user_id, platform=platform, platform_name=pname,
                       channel_name=payload.channel_name.strip(), channel_url=url, url_key=key,
                       initial_views=initial, approved_total_views=initial,
                       status="pending", created_at=ts, updated_at=ts)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _channel_dict(c)


def _own_channel(db, channel_id: int, user_id: int) -> CreatorChannel:
    c = (db.query(CreatorChannel)
         .filter(CreatorChannel.id == channel_id, CreatorChannel.user_id == user_id).first())
    if not c:
        raise HTTPException(status_code=404, detail="Channel not found.")
    return c


def _has_submissions(db, channel_id: int) -> bool:
    return db.query(CreatorReconciliationItem.id).filter(
        CreatorReconciliationItem.channel_id == channel_id).first() is not None


@router.put("/channels/{channel_id}/resubmit")
async def resubmit_channel(channel_id: int, payload: ChannelIn, db: Session = Depends(get_db),
                           current: User = Depends(require_hub)):
    """A rejected channel can be corrected and sent for approval again."""
    c = _own_channel(db, channel_id, current.user_id)
    if c.status != "rejected":
        raise HTTPException(status_code=400, detail="Only a rejected channel can be resubmitted.")
    url, platform, pname, key, initial = _clean_channel(db, payload, current.user_id, exclude_id=c.id)
    ts = now()
    db.add(CreatorChannelLog(channel_id=c.id, from_status=c.status, to_status="pending",
                             reason="Resubmitted by creator", actor_id=current.user_id, created_at=ts))
    c.platform, c.platform_name = platform, pname
    c.channel_name, c.channel_url, c.url_key = payload.channel_name.strip(), url, key
    c.initial_views = initial
    if not _has_submissions(db, c.id):
        c.approved_total_views = initial
    c.status, c.status_reason, c.is_primary = "pending", None, False
    c.created_at = c.created_at or ts
    c.updated_at = ts
    db.commit()
    db.refresh(c)
    return _channel_dict(c)


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: int, db: Session = Depends(get_db), current: User = Depends(require_hub)):
    """Remove a rejected channel from the list (channels with views submissions are kept)."""
    c = _own_channel(db, channel_id, current.user_id)
    if c.status != "rejected":
        raise HTTPException(status_code=400, detail="Only a rejected channel can be deleted.")
    if _has_submissions(db, c.id):
        raise HTTPException(status_code=400, detail="This channel has views submissions and can't be deleted.")
    db.query(CreatorChannelLog).filter(CreatorChannelLog.channel_id == c.id).delete(synchronize_session=False)
    db.delete(c)
    db.commit()
    return {"ok": True}


class PrimaryIn(BaseModel):
    channel_id: int


@router.put("/channels/primary")
async def set_primary(payload: PrimaryIn, db: Session = Depends(get_db), current: User = Depends(require_hub)):
    rows = db.query(CreatorChannel).filter(CreatorChannel.user_id == current.user_id).all()
    target = next((c for c in rows if c.id == payload.channel_id), None)
    if not target or target.status != "approved":
        raise HTTPException(status_code=400, detail="Only an approved channel can be your primary channel.")
    for c in rows:
        c.is_primary = (c.id == target.id)
    db.commit()
    return {"ok": True}


# ── Reconciliations ──────────────────────────────────────────────────────────

def _recon_dict(r: CreatorReconciliation, items, channels):
    return {
        "id": r.id, "status": r.status, "estimated_usd": r.estimated_usd,
        "approved_units": int(r.approved_units) if r.approved_units is not None else None,
        "reject_reason": r.reject_reason, "submitted_at": _iso(r.submitted_at), "reviewed_at": _iso(r.reviewed_at),
        "items": [{
            "channel_id": it.channel_id,
            "channel_name": channels[it.channel_id].channel_name if it.channel_id in channels else "",
            "platform": channels[it.channel_id].platform if it.channel_id in channels else "",
            "channel_type": it.channel_type, "previous_total": int(it.previous_total),
            "submitted_total": int(it.submitted_total), "period_views": int(it.period_views),
            "views_date": _iso(it.views_date), "rate_per_k": it.rate_per_k, "estimated_usd": it.estimated_usd,
            "proof_urls": proof_list(it.proof_urls),
        } for it in items],
    }


@router.get("/reconciliations")
async def list_reconciliations(db: Session = Depends(get_db), current: User = Depends(require_hub)):
    rs = (db.query(CreatorReconciliation).filter(CreatorReconciliation.user_id == current.user_id)
          .order_by(CreatorReconciliation.id.desc()).limit(50).all())
    ids = [r.id for r in rs]
    items = {}
    if ids:
        for it in db.query(CreatorReconciliationItem).filter(
                CreatorReconciliationItem.reconciliation_id.in_(ids)).all():
            items.setdefault(it.reconciliation_id, []).append(it)
    channels = {c.id: c for c in db.query(CreatorChannel).filter(CreatorChannel.user_id == current.user_id).all()}
    return [_recon_dict(r, items.get(r.id, []), channels) for r in rs]


class ReconItemIn(BaseModel):
    channel_id: int
    total_views: int = Field(..., ge=0)
    views_date: Optional[date] = None
    proof_urls: List[str] = Field(default_factory=list)   # from POST /creator-hub/proofs


# ── Proof of views (feedback 2026-11-10) ─────────────────────────────────────
# Screenshots or a short screen recording of the channel's total views, stored in R2 under
# creator_proofs/<random>. Only URLs we issued are accepted back in a submission.
PROOF_PREFIX = "creator_proofs/"
MAX_PROOFS_PER_CHANNEL = 5
MAX_PROOF_VIDEO_BYTES = int(os.getenv("CREATOR_PROOF_VIDEO_MAX_MB", "50")) * 1024 * 1024
MAX_PROOF_IMAGE_BYTES = 8 * 1024 * 1024


def proof_list(raw) -> list:
    try:
        v = json.loads(raw) if raw else []
        return [u for u in v if isinstance(u, str)]
    except (ValueError, TypeError):
        return []


def _sniff_video(data: bytes) -> Optional[tuple]:
    """(extension, content type) for MP4 / MOV / WEBM by magic bytes, else None."""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return (".mov", "video/quicktime") if data[8:10] == b"qt" else (".mp4", "video/mp4")
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm", "video/webm"
    return None


def _valid_proof_url(u: str) -> bool:
    from app.utils.r2_storage import R2_PUBLIC_URL
    return isinstance(u, str) and u.startswith(f"{R2_PUBLIC_URL}/images/{PROOF_PREFIX}") and len(u) <= 500


@router.post("/proofs")
async def upload_proof(file: UploadFile = File(...), current: User = Depends(require_hub)):
    data = await file.read(MAX_PROOF_VIDEO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty.")
    from app.utils.upload_security import sniff_image_ext
    ext = sniff_image_ext(data)
    if ext:
        if len(data) > MAX_PROOF_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Images must be 8 MB or smaller.")
        ctype = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
                 ".webp": "image/webp", ".bmp": "image/bmp"}.get(ext, "application/octet-stream")
        kind = "image"
    else:
        vid = _sniff_video(data)
        if not vid:
            raise HTTPException(status_code=400, detail="Please upload an image (JPG, PNG, WEBP) or a video (MP4, MOV, WEBM).")
        if len(data) > MAX_PROOF_VIDEO_BYTES:
            raise HTTPException(status_code=413, detail=f"Videos must be {MAX_PROOF_VIDEO_BYTES // (1024 * 1024)} MB or smaller.")
        ext, ctype = vid
        kind = "video"
    try:
        from app.utils.r2_storage import upload_image_to_r2
        url = upload_image_to_r2(data, f"{PROOF_PREFIX}{current.user_id}_{uuid.uuid4().hex}{ext}", ctype)
    except Exception:
        raise HTTPException(status_code=502, detail="Upload failed. Please try again.")
    return {"url": url, "kind": kind}


class ReconIn(BaseModel):
    primary_channel_id: Optional[int] = None
    items: List[ReconItemIn]


@router.post("/reconciliations")
async def submit_reconciliation(payload: ReconIn, db: Session = Depends(get_db), current: User = Depends(require_hub)):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Enter the total views of at least one channel.")
    if _open_recon(db, current.user_id):
        raise HTTPException(status_code=400, detail="Your previous submission is still being reviewed.")
    nxt = next_submit_at(db, current.user_id)
    if nxt:
        raise HTTPException(status_code=400, detail=(
            f"Views can be submitted once every {SUBMIT_MIN_DAYS} days. "
            f"Your next submission opens on {nxt.strftime('%d/%m/%Y')}."))
    channels = {c.id: c for c in db.query(CreatorChannel).filter(CreatorChannel.user_id == current.user_id).all()}
    approved = [c for c in channels.values() if c.status == "approved"]
    if not approved:
        raise HTTPException(status_code=400, detail="You need at least one approved channel to submit views.")

    # Primary channel: the only approved one, or the creator's choice among approved ones.
    if len(approved) == 1:
        primary_id = approved[0].id
    else:
        primary_id = payload.primary_channel_id or next((c.id for c in approved if c.is_primary), None)
        if primary_id not in {c.id for c in approved}:
            raise HTTPException(status_code=400, detail="Choose your primary channel for this period.")

    seen, lines, total_usd = set(), [], 0.0
    today = now().date()
    for it in payload.items:
        c = channels.get(it.channel_id)
        if not c or it.channel_id in seen:
            raise HTTPException(status_code=400, detail="Invalid channel in your submission.")
        seen.add(it.channel_id)
        if c.status != "approved":
            raise HTTPException(status_code=400, detail=f"“{c.channel_name}” is not approved, so its views can't be submitted.")
        prev = int(c.approved_total_views or 0)
        if it.total_views < prev:
            raise HTTPException(status_code=400, detail=(
                f"Total views for “{c.channel_name}” can't be lower than the last approved total ({prev:,}). "
                "If the platform reduced your views, please contact us."))
        if it.views_date and it.views_date > today:
            raise HTTPException(status_code=400, detail="The views date can't be in the future.")
        proofs = [u.strip() for u in (it.proof_urls or []) if u and u.strip()]
        if len(proofs) > MAX_PROOFS_PER_CHANNEL or not all(_valid_proof_url(u) for u in proofs):
            raise HTTPException(status_code=400, detail="Invalid proof file. Please upload it again.")
        ctype = "primary" if c.id == primary_id else "secondary"
        period = it.total_views - prev
        usd = reward_usd(period, ctype)
        total_usd += usd
        lines.append((c, ctype, prev, it.total_views, period, it.views_date or today, usd, proofs))

    ts = now()
    r = CreatorReconciliation(user_id=current.user_id, status="submitted", estimated_usd=round(total_usd, 2),
                              submitted_at=ts)
    db.add(r)
    db.flush()
    for c, ctype, prev, total, period, vdate, usd, proofs in lines:
        db.add(CreatorReconciliationItem(
            reconciliation_id=r.id, channel_id=c.id, channel_type=ctype, previous_total=prev,
            submitted_total=total, period_views=period, views_date=vdate,
            rate_per_k=PRIMARY_RATE_PER_K if ctype == "primary" else SECONDARY_RATE_PER_K, estimated_usd=usd,
            proof_urls=json.dumps(proofs) if proofs else None))
        c.last_submitted_total, c.last_submitted_at = total, ts
    for c in channels.values():   # remember the choice for next period
        c.is_primary = (c.id == primary_id)
    db.commit()
    return {"ok": True, "id": r.id, "estimated_usd": r.estimated_usd}


@router.get("/earnings")
async def earnings(db: Session = Depends(get_db), current: User = Depends(require_hub)):
    approved_units = db.query(func.coalesce(func.sum(AffiliateWalletTx.amount), 0)).filter(
        AffiliateWalletTx.user_id == current.user_id, AffiliateWalletTx.type == "creator_reward").scalar() or 0
    pending_usd = db.query(func.coalesce(func.sum(CreatorReconciliation.estimated_usd), 0)).filter(
        CreatorReconciliation.user_id == current.user_id,
        CreatorReconciliation.status.in_(OPEN_RECON_STATUSES)).scalar() or 0
    approved_views = db.query(func.coalesce(func.sum(CreatorReconciliationItem.period_views), 0)).join(
        CreatorReconciliation, CreatorReconciliation.id == CreatorReconciliationItem.reconciliation_id).filter(
        CreatorReconciliation.user_id == current.user_id, CreatorReconciliation.status == "approved").scalar() or 0
    return {
        "approved_rewards": int(approved_units),          # wallet minor units
        "pending_rewards_usd": round(float(pending_usd), 2),
        "approved_views": int(approved_views),
        "balance": int(current.affiliate_balance or 0),
        "currency": AFFILIATE_CURRENCY, "unit_divisor": UNIT_DIVISOR,
    }


@router.get("/leaderboard")
async def leaderboard(period: str = Query("lifetime", pattern="^(lifetime|monthly)$"),
                      month: Optional[str] = Query(None, max_length=7),
                      db: Session = Depends(get_db), current: User = Depends(require_hub)):
    """Creator Leaderboard shown inside Creator Hub. Other creators' names are
    abbreviated (your own row shows in full) — no emails or contact details."""
    data = build_leaderboard(db, period, month)
    for r in data["items"]:
        r["is_me"] = r["user_id"] == current.user_id
        if not r["is_me"]:
            r["name"] = abbreviate_name(r["name"])
        r.pop("user_id", None)
    data["months"] = available_months(db)
    return data
