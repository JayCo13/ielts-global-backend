"""Creator Hub — admin side (2026-11). All under /admin/creator-hub.

Applications   POST applications/{id}/approve | reject
Access         GET access, POST access/{user_id}, GET access/{user_id}/logs
Creators       GET creators (overview), GET creators/{user_id} (profile detail)
Channels       GET channels, POST channels/{id}/status, GET channels/{id}/logs
Reconciliation GET reconciliations, POST reconciliations/{id}/review | approve | reject
Earnings       GET earnings, GET earnings/transactions
Leaderboard    GET leaderboard (lifetime | monthly)
Bonus settings GET / PUT bonus-settings (tiers, per-video reward, monthly cap — 2026-11-19)

Admin-facing messages are Vietnamese (admin UI convention).
"""
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import (
    User, CreatorApplication, CreatorProfile, CreatorAccessLog, CreatorChannel, CreatorChannelLog,
    CreatorReconciliation, CreatorReconciliationItem, AffiliateWalletTx, AffiliateWithdrawal,
    CreatorVideoLink,
)
from app.utils import creator_bonus
from app.routes.admin.auth import get_current_admin
from app.utils.affiliate import AFFILIATE_CURRENCY, UNIT_DIVISOR, format_amount
from app.utils import creator_hub_email as hub_mail
from app.utils.creator_hub import (
    PLATFORMS, CHANNEL_STATUSES, OPEN_RECON_STATUSES, PENDING_APP_STATUSES,
    now, app_status_label, get_profile, set_hub_access, usd_to_units, platform_label,
)
from app.utils.creator_leaderboard import build_leaderboard, available_months
from app.routes.customer.creator_hub import proof_list

router = APIRouter(prefix="/creator-hub")


def _iso(d):
    return d.isoformat() if d else None


def _money_meta():
    return {"currency": AFFILIATE_CURRENCY, "unit_divisor": UNIT_DIVISOR}


def _need_reason(reason: Optional[str]) -> str:
    r = (reason or "").strip()
    if not r:
        raise HTTPException(status_code=400, detail="Vui lòng ghi rõ lý do.")
    return r[:2000]


def _contact(db, user: Optional[User]):
    """(email, display name) for creator emails: account email first, hub profile as fallback."""
    if not user:
        return None, None
    p = get_profile(db, user.user_id)
    return (user.email or (p.email if p else None)), ((p.full_name if p else None) or user.username)


# ── Applications ──────────────────────────────────────────────────────────────

def _resolve_account(db, a: CreatorApplication, user_id: Optional[int]) -> Optional[User]:
    if user_id:
        return db.query(User).filter(User.user_id == user_id).first()
    if a.user_id:
        return db.query(User).filter(User.user_id == a.user_id).first()
    if a.email:
        return (db.query(User).filter(func.lower(User.email) == a.email.strip().lower(),
                                      User.email_verified == True).first())  # noqa: E712
    return None


class ApproveIn(BaseModel):
    user_id: Optional[int] = None       # link a specific account (logged-out landing applications)


@router.post("/applications/{app_id}/approve")
async def approve_application(app_id: int, payload: ApproveIn, bg: BackgroundTasks, db: Session = Depends(get_db),
                              admin: User = Depends(get_current_admin)):
    a = db.query(CreatorApplication).filter(CreatorApplication.id == app_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn.")
    if a.status == "approved":
        raise HTTPException(status_code=400, detail="Đơn này đã được duyệt.")
    user = _resolve_account(db, a, payload.user_id)
    if not user:
        raise HTTPException(status_code=400, detail=(
            "Đơn này chưa gắn với tài khoản nào (người gửi chưa đăng nhập, email chưa khớp tài khoản đã xác minh). "
            "Hãy nhập tên tài khoản để gắn, hoặc nhờ họ tạo tài khoản bằng email này rồi duyệt lại."))
    ts = now()
    a.user_id = user.user_id
    a.status, a.reject_reason, a.reviewed_at, a.reviewed_by, a.updated_at = "approved", None, ts, admin.user_id, ts
    set_hub_access(db, user, True, "application", actor_id=admin.user_id, application=a,
                   note=f"Duyệt đơn #{a.id}")
    db.commit()
    to, name = _contact(db, user)
    bg.add_task(hub_mail.application_approved, to, a.full_name or name)
    return {"ok": True, "user_id": user.user_id, "username": user.username}


class ReasonIn(BaseModel):
    reason: Optional[str] = None


@router.post("/applications/{app_id}/reject")
async def reject_application(app_id: int, payload: ReasonIn, bg: BackgroundTasks, db: Session = Depends(get_db),
                             admin: User = Depends(get_current_admin)):
    a = db.query(CreatorApplication).filter(CreatorApplication.id == app_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn.")
    if a.status == "approved":
        raise HTTPException(status_code=400, detail="Đơn đã duyệt — muốn thu hồi thì tắt quyền Creator Hub ở tab Quyền truy cập.")
    ts = now()
    a.status, a.reject_reason, a.reviewed_at, a.reviewed_by, a.updated_at = (
        "rejected", _need_reason(payload.reason), ts, admin.user_id, ts)
    db.commit()
    linked = db.query(User).filter(User.user_id == a.user_id).first() if a.user_id else None
    to = (linked.email if linked else None) or a.email
    bg.add_task(hub_mail.application_rejected, to, a.full_name or (linked.username if linked else None), a.reject_reason)
    return {"ok": True}


# ── Creator Hub Access ───────────────────────────────────────────────────────

def _latest_apps(db, user_ids):
    out = {}
    if not user_ids:
        return out
    for a in (db.query(CreatorApplication).filter(CreatorApplication.user_id.in_(user_ids))
              .order_by(CreatorApplication.created_at.asc(), CreatorApplication.id.asc()).all()):
        out[a.user_id] = a          # last one wins
    return out


@router.get("/access")
async def list_access(q: Optional[str] = Query(None), only: Optional[str] = Query(None),
                      page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
                      db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Without q: accounts that have a creator profile or an application. With q: search any account."""
    query = db.query(User)
    if q and q.strip():
        like = f"%{q.strip()}%"
        prof_ids = db.query(CreatorProfile.user_id).filter(CreatorProfile.phone.ilike(like))
        query = query.filter(or_(User.username.ilike(like), User.email.ilike(like), User.user_id.in_(prof_ids)))
    else:
        ids_p = db.query(CreatorProfile.user_id)
        ids_a = db.query(CreatorApplication.user_id).filter(CreatorApplication.user_id.isnot(None))
        query = query.filter(or_(User.user_id.in_(ids_p), User.user_id.in_(ids_a)))
    if only == "enabled":
        query = query.filter(User.user_id.in_(db.query(CreatorProfile.user_id).filter(CreatorProfile.hub_access == True)))  # noqa: E712
    total = query.count()
    users = query.order_by(User.user_id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    ids = [u.user_id for u in users]
    profs = {p.user_id: p for p in db.query(CreatorProfile).filter(CreatorProfile.user_id.in_(ids)).all()} if ids else {}
    apps = _latest_apps(db, ids)
    items = []
    for u in users:
        p, a = profs.get(u.user_id), apps.get(u.user_id)
        items.append({
            "user_id": u.user_id, "username": u.username, "email": u.email, "role": u.role,
            "phone": (p.phone if p else None) or (a.phone if a else None),
            "full_name": (p.full_name if p else None) or (a.full_name if a else None),
            "application_status": app_status_label(a.status) if a else "not_applied",
            "hub_access": bool(p and p.hub_access),
            "access_source": p.access_source if p else None,
            "registration_complete": bool(p and p.full_name and p.phone),
            "enabled_at": _iso(p.enabled_at) if p else None,
            "discount_enabled": bool(p and p.discount_enabled),
        })
    return {"total": total, "page": page, "page_size": page_size, "items": items}


class DiscountIn(BaseModel):
    enabled: bool


@router.post("/access/{user_id}/discount")
async def set_discount_permission(user_id: int, payload: DiscountIn, db: Session = Depends(get_db),
                                  admin: User = Depends(get_current_admin)):
    """Discount Permission (2026-11-14): ON = buyers referred by this creator get 5% off
    their first VIP order. Independent of Creator Hub access / commission."""
    u = db.query(User).filter(User.user_id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
    p = get_profile(db, user_id)
    if not p:
        ts = now()
        p = CreatorProfile(user_id=user_id, hub_access=False, created_at=ts, updated_at=ts, email=u.email)
        db.add(p)
    p.discount_enabled = bool(payload.enabled)
    p.updated_at = now()
    db.commit()
    return {"ok": True, "discount_enabled": p.discount_enabled}


class AccessIn(BaseModel):
    enabled: bool
    note: Optional[str] = None


@router.post("/access/{user_id}")
async def set_access(user_id: int, payload: AccessIn, bg: BackgroundTasks, db: Session = Depends(get_db),
                     admin: User = Depends(get_current_admin)):
    u = db.query(User).filter(User.user_id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
    had_access = bool((get_profile(db, user_id) or CreatorProfile()).hub_access)
    set_hub_access(db, u, payload.enabled, "admin_direct", actor_id=admin.user_id,
                   note=(payload.note or "").strip()[:1000] or None)
    db.commit()
    if had_access != bool(payload.enabled):      # email only on a real change
        to, name = _contact(db, u)
        bg.add_task(hub_mail.access_changed, to, name, bool(payload.enabled), (payload.note or "").strip() or None)
    return {"ok": True, "hub_access": payload.enabled}


@router.get("/access/{user_id}/logs")
async def access_logs(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    rows = (db.query(CreatorAccessLog).filter(CreatorAccessLog.user_id == user_id)
            .order_by(CreatorAccessLog.id.desc()).limit(100).all())
    actors = {u.user_id: u.username for u in db.query(User).filter(
        User.user_id.in_({r.actor_id for r in rows if r.actor_id})).all()} if rows else {}
    return [{"action": r.action, "source": r.source, "note": r.note, "actor": actors.get(r.actor_id),
             "created_at": _iso(r.created_at)} for r in rows]


# ── Creators overview / profile ──────────────────────────────────────────────

def _sum_by_user(db, col, *filters):
    return dict(db.query(AffiliateWalletTx.user_id, func.coalesce(func.sum(col), 0))
                .filter(*filters).group_by(AffiliateWalletTx.user_id).all())


@router.get("/creators")
async def creators_overview(q: Optional[str] = Query(None), db: Session = Depends(get_db),
                            admin: User = Depends(get_current_admin)):
    query = db.query(CreatorProfile, User).join(User, User.user_id == CreatorProfile.user_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.username.ilike(like), User.email.ilike(like),
                                 CreatorProfile.full_name.ilike(like), CreatorProfile.phone.ilike(like)))
    rows = query.order_by(CreatorProfile.enabled_at.desc()).limit(500).all()
    ids = [p.user_id for p, _u in rows]
    if not ids:
        return {"items": [], **_money_meta()}
    ch_counts = {}
    for uid, st, n in (db.query(CreatorChannel.user_id, CreatorChannel.status, func.count(CreatorChannel.id))
                       .filter(CreatorChannel.user_id.in_(ids)).group_by(CreatorChannel.user_id, CreatorChannel.status).all()):
        ch_counts.setdefault(uid, {})[st] = n
    declared = dict(db.query(CreatorChannel.user_id, func.coalesce(func.sum(CreatorChannel.last_submitted_total), 0))
                    .filter(CreatorChannel.user_id.in_(ids)).group_by(CreatorChannel.user_id).all())
    approved_views = dict(db.query(CreatorChannel.user_id, func.coalesce(func.sum(CreatorChannel.approved_total_views), 0))
                          .filter(CreatorChannel.user_id.in_(ids)).group_by(CreatorChannel.user_id).all())
    rewards = _sum_by_user(db, AffiliateWalletTx.amount, AffiliateWalletTx.user_id.in_(ids),
                           AffiliateWalletTx.type == "creator_reward")
    commissions = _sum_by_user(db, AffiliateWalletTx.amount, AffiliateWalletTx.user_id.in_(ids),
                               AffiliateWalletTx.type == "commission")
    apps = _latest_apps(db, ids)
    items = []
    for p, u in rows:
        c = ch_counts.get(u.user_id, {})
        a = apps.get(u.user_id)
        items.append({
            "user_id": u.user_id, "username": u.username, "email": p.email or u.email, "full_name": p.full_name,
            "phone": p.phone, "application_status": app_status_label(a.status) if a else "not_applied",
            "hub_access": bool(p.hub_access), "access_source": p.access_source, "enabled_at": _iso(p.enabled_at),
            "channels_total": sum(c.values()), "channels_approved": c.get("approved", 0),
            "channels_pending": c.get("pending", 0),
            "declared_views": int(declared.get(u.user_id, 0) or 0),
            "approved_views": int(approved_views.get(u.user_id, 0) or 0),
            "creator_rewards": int(rewards.get(u.user_id, 0) or 0),
            "affiliate_commission": int(commissions.get(u.user_id, 0) or 0),
            "wallet_balance": int(u.affiliate_balance or 0),
        })
    return {"items": items, **_money_meta()}


def _channel_row(c: CreatorChannel, user: Optional[User] = None, profile: Optional[CreatorProfile] = None, dups=0):
    return {
        "id": c.id, "user_id": c.user_id, "username": user.username if user else None,
        "full_name": profile.full_name if profile else None,
        "platform": c.platform, "platform_label": platform_label(c), "platform_name": c.platform_name,
        "initial_views": int(c.initial_views or 0),
        "channel_name": c.channel_name, "channel_url": c.channel_url, "status": c.status,
        "status_reason": c.status_reason, "is_primary": bool(c.is_primary),
        "approved_total_views": int(c.approved_total_views or 0),
        "last_submitted_total": int(c.last_submitted_total) if c.last_submitted_total is not None else None,
        "registered_at": _iso(c.created_at), "duplicates": dups,
    }


def _recon_rows(db, rs):
    ids = [r.id for r in rs]
    items = {}
    if ids:
        for it in db.query(CreatorReconciliationItem).filter(CreatorReconciliationItem.reconciliation_id.in_(ids)).all():
            items.setdefault(it.reconciliation_id, []).append(it)
    ch_ids = {it.channel_id for lst in items.values() for it in lst}
    chans = {c.id: c for c in db.query(CreatorChannel).filter(CreatorChannel.id.in_(ch_ids)).all()} if ch_ids else {}
    uids = {r.user_id for r in rs}
    users = {u.user_id: u for u in db.query(User).filter(User.user_id.in_(uids)).all()} if uids else {}
    videos = creator_bonus.video_rows(db, ids)
    # Feedback 2026-11-10: show the creator's previous submission time and the gap in days.
    prev_at = {}
    for r in rs:
        if not r.submitted_at:
            continue
        prev = (db.query(CreatorReconciliation.submitted_at)
                .filter(CreatorReconciliation.user_id == r.user_id, CreatorReconciliation.id != r.id,
                        CreatorReconciliation.submitted_at < r.submitted_at)
                .order_by(CreatorReconciliation.submitted_at.desc()).first())
        prev_at[r.id] = prev[0] if prev else None
    out = []
    for r in rs:
        u = users.get(r.user_id)
        p_at = prev_at.get(r.id)
        out.append({
            "id": r.id, "user_id": r.user_id, "username": u.username if u else None,
            "status": r.status, "estimated_usd": r.estimated_usd,
            "previous_submitted_at": _iso(p_at),
            "days_since_previous": ((r.submitted_at - p_at).total_seconds() / 86400.0
                                    if p_at and r.submitted_at else None),
            "approved_units": int(r.approved_units) if r.approved_units is not None else None,
            "reject_reason": r.reject_reason, "submitted_at": _iso(r.submitted_at), "reviewed_at": _iso(r.reviewed_at),
            "wallet_tx_id": r.wallet_tx_id,
            # Bonus policy 2026-11-19 (NULL on older submissions).
            "views_reward_raw_usd": r.views_reward_raw_usd, "views_reward_usd": r.views_reward_usd,
            "tier_bonus_usd": r.tier_bonus_usd, "video_reward_usd": r.video_reward_usd,
            "videos": [{"id": v.id, "url": v.url, "status": v.status, "reward_usd": v.reward_usd}
                       for v in videos.get(r.id, [])],
            "items": [{
                "channel_id": it.channel_id,
                "channel_name": chans[it.channel_id].channel_name if it.channel_id in chans else "",
                "channel_url": chans[it.channel_id].channel_url if it.channel_id in chans else "",
                "channel_status": chans[it.channel_id].status if it.channel_id in chans else "",
                "platform": platform_label(chans[it.channel_id]) if it.channel_id in chans else "",
                "channel_type": it.channel_type, "previous_total": int(it.previous_total),
                "submitted_total": int(it.submitted_total), "period_views": int(it.period_views),
                "views_date": _iso(it.views_date), "rate_per_k": it.rate_per_k, "estimated_usd": it.estimated_usd,
                "proof_urls": proof_list(it.proof_urls),
                "tier_views": int(it.tier_views) if it.tier_views is not None else None,
                "tier_bonus_usd": it.tier_bonus_usd,
            } for it in items.get(r.id, [])],
        })
    return out


@router.get("/creators/{user_id}")
async def creator_detail(user_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    u = db.query(User).filter(User.user_id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
    p = get_profile(db, user_id)
    apps = (db.query(CreatorApplication).filter(CreatorApplication.user_id == user_id)
            .order_by(CreatorApplication.id.desc()).all())
    chans = db.query(CreatorChannel).filter(CreatorChannel.user_id == user_id).order_by(CreatorChannel.id).all()
    rs = (db.query(CreatorReconciliation).filter(CreatorReconciliation.user_id == user_id)
          .order_by(CreatorReconciliation.id.desc()).limit(100).all())
    txs = (db.query(AffiliateWalletTx).filter(AffiliateWalletTx.user_id == user_id)
           .order_by(AffiliateWalletTx.id.desc()).limit(200).all())
    return {
        "user": {"user_id": u.user_id, "username": u.username, "email": u.email,
                 "wallet_balance": int(u.affiliate_balance or 0)},
        "profile": ({"full_name": p.full_name, "phone": p.phone, "email": p.email, "country_code": p.country_code,
                     "hub_access": bool(p.hub_access), "access_source": p.access_source,
                     "enabled_at": _iso(p.enabled_at)} if p else None),
        "applications": [{"id": a.id, "status": app_status_label(a.status), "full_name": a.full_name,
                          "email": a.email, "phone": a.phone, "reject_reason": a.reject_reason,
                          "source": a.source or "landing", "created_at": _iso(a.created_at),
                          "reviewed_at": _iso(a.reviewed_at)} for a in apps],
        "channels": [_channel_row(c, u, p) for c in chans],
        "reconciliations": _recon_rows(db, rs),
        "wallet": [{"id": t.id, "type": t.type, "amount": int(t.amount), "balance_after": int(t.balance_after),
                    "description": t.description, "created_at": _iso(t.created_at)} for t in txs],
        **_money_meta(),
    }


# ── Channels ─────────────────────────────────────────────────────────────────

@router.get("/channels")
async def list_channels(status: Optional[str] = Query(None), platform: Optional[str] = Query(None),
                        q: Optional[str] = Query(None), page: int = Query(1, ge=1),
                        page_size: int = Query(50, ge=1, le=200),
                        db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    query = db.query(CreatorChannel, User).join(User, User.user_id == CreatorChannel.user_id)
    if status in CHANNEL_STATUSES:
        query = query.filter(CreatorChannel.status == status)
    if platform in PLATFORMS:
        query = query.filter(CreatorChannel.platform == platform)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.username.ilike(like), User.email.ilike(like),
                                 CreatorChannel.channel_name.ilike(like), CreatorChannel.channel_url.ilike(like)))
    total = query.count()
    rows = (query.order_by(CreatorChannel.created_at.desc(), CreatorChannel.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    keys = {c.url_key for c, _u in rows if c.url_key}
    dup = {}
    if keys:
        for key, uid in (db.query(CreatorChannel.url_key, CreatorChannel.user_id)
                         .filter(CreatorChannel.url_key.in_(keys), CreatorChannel.status != "rejected").all()):
            dup.setdefault(key, set()).add(uid)
    uids = {u.user_id for _c, u in rows}
    profs = {p.user_id: p for p in db.query(CreatorProfile).filter(CreatorProfile.user_id.in_(uids)).all()} if uids else {}
    counts = dict(db.query(CreatorChannel.status, func.count(CreatorChannel.id)).group_by(CreatorChannel.status).all())
    return {
        "total": total, "counts": counts,
        "items": [_channel_row(c, u, profs.get(u.user_id),
                               dups=len(dup.get(c.url_key, set()) - {c.user_id})) for c, u in rows],
    }


class ChannelActionIn(BaseModel):
    action: str                 # approve / reject / suspend / reactivate
    reason: Optional[str] = None


_TRANSITIONS = {
    "approve": ({"pending", "rejected"}, "approved"),
    "reject": ({"pending"}, "rejected"),
    "suspend": ({"approved"}, "suspended"),
    "reactivate": ({"suspended"}, "approved"),
}


@router.post("/channels/{channel_id}/status")
async def channel_action(channel_id: int, payload: ChannelActionIn, bg: BackgroundTasks, db: Session = Depends(get_db),
                         admin: User = Depends(get_current_admin)):
    c = db.query(CreatorChannel).filter(CreatorChannel.id == channel_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh.")
    if payload.action not in _TRANSITIONS:
        raise HTTPException(status_code=400, detail="Thao tác không hợp lệ.")
    allowed_from, to = _TRANSITIONS[payload.action]
    if c.status not in allowed_from:
        raise HTTPException(status_code=400, detail=f"Kênh đang ở trạng thái '{c.status}', không thể {payload.action}.")
    reason = _need_reason(payload.reason) if payload.action in ("reject", "suspend") else ((payload.reason or "").strip() or None)
    ts = now()
    db.add(CreatorChannelLog(channel_id=c.id, from_status=c.status, to_status=to, reason=reason,
                             actor_id=admin.user_id, created_at=ts))
    c.status = to
    c.status_reason = reason if to in ("rejected", "suspended") else None
    if to != "approved":
        c.is_primary = False
    c.reviewed_at, c.reviewed_by, c.updated_at = ts, admin.user_id, ts
    db.commit()
    mail_to, name = _contact(db, db.query(User).filter(User.user_id == c.user_id).first())
    kind = "reactivated" if payload.action == "reactivate" else c.status
    bg.add_task(hub_mail.channel_status, mail_to, name, platform_label(c), c.channel_name, kind, reason)
    return {"ok": True, "status": c.status}


@router.get("/channels/{channel_id}/logs")
async def channel_logs(channel_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    rows = (db.query(CreatorChannelLog).filter(CreatorChannelLog.channel_id == channel_id)
            .order_by(CreatorChannelLog.id.desc()).all())
    actors = {u.user_id: u.username for u in db.query(User).filter(
        User.user_id.in_({r.actor_id for r in rows if r.actor_id})).all()} if rows else {}
    return [{"from_status": r.from_status, "to_status": r.to_status, "reason": r.reason,
             "actor": actors.get(r.actor_id), "created_at": _iso(r.created_at)} for r in rows]


# ── Reconciliations ──────────────────────────────────────────────────────────

@router.get("/reconciliations")
async def list_reconciliations(status: Optional[str] = Query(None), user_id: Optional[int] = Query(None),
                               q: Optional[str] = Query(None), page: int = Query(1, ge=1),
                               page_size: int = Query(30, ge=1, le=100),
                               db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    query = db.query(CreatorReconciliation)
    if status == "open":
        query = query.filter(CreatorReconciliation.status.in_(OPEN_RECON_STATUSES))
    elif status:
        query = query.filter(CreatorReconciliation.status == status)
    if user_id:
        query = query.filter(CreatorReconciliation.user_id == user_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(CreatorReconciliation.user_id.in_(
            db.query(User.user_id).filter(or_(User.username.ilike(like), User.email.ilike(like)))))
    total = query.count()
    rs = (query.order_by(CreatorReconciliation.submitted_at.desc(), CreatorReconciliation.id.desc())
          .offset((page - 1) * page_size).limit(page_size).all())
    counts = dict(db.query(CreatorReconciliation.status, func.count(CreatorReconciliation.id))
                  .group_by(CreatorReconciliation.status).all())
    return {"total": total, "counts": counts, "items": _recon_rows(db, rs), **_money_meta()}


def _claim(db, rid: int, new_status: str, admin_id: int, extra=None) -> int:
    """Atomically move an OPEN reconciliation to new_status. Returns rows changed
    (0 = already processed by someone else) — this is the double-approval guard."""
    vals = {"status": new_status, "reviewed_at": now(), "reviewed_by": admin_id}
    vals.update(extra or {})
    return (db.query(CreatorReconciliation)
            .filter(CreatorReconciliation.id == rid, CreatorReconciliation.status.in_(OPEN_RECON_STATUSES))
            .update(vals, synchronize_session=False))


@router.post("/reconciliations/{rid}/review")
async def mark_under_review(rid: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    n = (db.query(CreatorReconciliation)
         .filter(CreatorReconciliation.id == rid, CreatorReconciliation.status == "submitted")
         .update({"status": "under_review"}, synchronize_session=False))
    db.commit()
    if not n:
        raise HTTPException(status_code=400, detail="Yêu cầu không còn ở trạng thái 'Đã gửi'.")
    return {"ok": True}


class ApproveReconIn(BaseModel):
    # Individual Video Bonus: ids of the video links the admin accepts; the other pending
    # links of this submission are rejected. None (older admin UI) = accept all.
    approved_video_ids: Optional[List[int]] = None


@router.post("/reconciliations/{rid}/approve")
async def approve_reconciliation(rid: int, bg: BackgroundTasks, payload: Optional[ApproveReconIn] = None,
                                 db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    r = db.query(CreatorReconciliation).filter(CreatorReconciliation.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu đối soát.")
    if not _claim(db, rid, "approved", admin.user_id):
        db.rollback()
        raise HTTPException(status_code=400, detail="Yêu cầu này đã được xử lý rồi.")
    try:
        items = db.query(CreatorReconciliationItem).filter(CreatorReconciliationItem.reconciliation_id == rid).all()
        for it in items:
            ch = db.query(CreatorChannel).filter(CreatorChannel.id == it.channel_id).with_for_update().first()
            if ch is None:
                continue
            if int(ch.approved_total_views or 0) != int(it.previous_total):
                raise HTTPException(status_code=409, detail=(
                    f"Mốc views đã duyệt của kênh '{ch.channel_name}' đã thay đổi kể từ khi gửi — hãy từ chối và để creator gửi lại."))
            ch.approved_total_views = int(it.submitted_total)
            ch.updated_at = now()
        if r.views_reward_usd is None:      # submitted before the bonus policy (2026-11-19)
            usd = round(sum(float(it.estimated_usd or 0) for it in items), 2)
        else:
            vids = (db.query(CreatorVideoLink)
                    .filter(CreatorVideoLink.reconciliation_id == rid, CreatorVideoLink.status == "pending").all())
            keep = None if payload is None or payload.approved_video_ids is None else set(payload.approved_video_ids)
            video_usd = 0.0
            for v in vids:
                ok = keep is None or v.id in keep
                v.status, v.reviewed_at = ("approved" if ok else "rejected"), now()
                if ok:
                    video_usd += float(v.reward_usd or 0)
            usd = round(float(r.views_reward_usd or 0) + float(r.tier_bonus_usd or 0) + video_usd, 2)
        units = usd_to_units(usd)
        tx_id = None
        if units > 0:
            user = db.query(User).filter(User.user_id == r.user_id).with_for_update().first()
            user.affiliate_balance = int(user.affiliate_balance or 0) + units
            tx = AffiliateWalletTx(
                user_id=user.user_id, type="creator_reward", amount=units, balance_after=user.affiliate_balance,
                description=f"Creator reward · views reconciliation #{rid} (${usd:,.2f})")
            db.add(tx)
            db.flush()
            tx_id = tx.id
        (db.query(CreatorReconciliation).filter(CreatorReconciliation.id == rid)
         .update({"approved_units": units, "wallet_tx_id": tx_id, "reject_reason": None}, synchronize_session=False))
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Không duyệt được, chưa cộng tiền. Thử lại sau.")
    to, name = _contact(db, db.query(User).filter(User.user_id == r.user_id).first())
    bg.add_task(hub_mail.reconciliation_approved, to, name, rid, usd, format_amount(units))
    return {"ok": True, "approved_units": units, "usd": usd}


@router.post("/reconciliations/{rid}/reject")
async def reject_reconciliation(rid: int, payload: ReasonIn, bg: BackgroundTasks, db: Session = Depends(get_db),
                                admin: User = Depends(get_current_admin)):
    reason = _need_reason(payload.reason)
    if not _claim(db, rid, "rejected", admin.user_id, {"reject_reason": reason}):
        db.rollback()
        raise HTTPException(status_code=400, detail="Yêu cầu này đã được xử lý rồi.")
    creator_bonus.release_videos(db, rid)
    db.commit()
    r = db.query(CreatorReconciliation).filter(CreatorReconciliation.id == rid).first()
    to, name = _contact(db, db.query(User).filter(User.user_id == r.user_id).first() if r else None)
    bg.add_task(hub_mail.reconciliation_rejected, to, name, rid, reason)
    return {"ok": True}


# ── Earnings ─────────────────────────────────────────────────────────────────

@router.get("/earnings")
async def earnings(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    approved = _sum_by_user(db, AffiliateWalletTx.amount, AffiliateWalletTx.type == "creator_reward")
    pending = dict(db.query(CreatorReconciliation.user_id, func.coalesce(func.sum(CreatorReconciliation.estimated_usd), 0))
                   .filter(CreatorReconciliation.status.in_(OPEN_RECON_STATUSES))
                   .group_by(CreatorReconciliation.user_id).all())
    views = dict(db.query(CreatorChannel.user_id, func.coalesce(func.sum(CreatorChannel.approved_total_views), 0))
                 .group_by(CreatorChannel.user_id).all())
    paid = dict(db.query(AffiliateWithdrawal.user_id, func.coalesce(func.sum(AffiliateWithdrawal.amount), 0))
                .filter(AffiliateWithdrawal.status == "paid").group_by(AffiliateWithdrawal.user_id).all())
    enabled = {uid for (uid,) in db.query(CreatorProfile.user_id).filter(CreatorProfile.hub_access == True).all()}  # noqa: E712
    ids = set(approved) | set(pending) | enabled
    users = {u.user_id: u for u in db.query(User).filter(User.user_id.in_(ids)).all()} if ids else {}
    items = [{
        "user_id": uid, "username": users[uid].username if uid in users else None,
        "approved_rewards": int(approved.get(uid, 0) or 0),
        "pending_rewards_usd": round(float(pending.get(uid, 0) or 0), 2),
        "approved_views": int(views.get(uid, 0) or 0),
        "wallet_balance": int(users[uid].affiliate_balance or 0) if uid in users else 0,
        "withdrawn_paid": int(paid.get(uid, 0) or 0),
    } for uid in ids]
    items.sort(key=lambda x: (x["approved_rewards"], x["pending_rewards_usd"]), reverse=True)
    return {"items": items, **_money_meta()}


@router.get("/earnings/transactions")
async def earning_transactions(user_id: Optional[int] = Query(None), db: Session = Depends(get_db),
                               admin: User = Depends(get_current_admin)):
    query = db.query(CreatorReconciliation).filter(CreatorReconciliation.status == "approved")
    if user_id:
        query = query.filter(CreatorReconciliation.user_id == user_id)
    rs = query.order_by(CreatorReconciliation.reviewed_at.desc()).limit(200).all()
    return {"items": _recon_rows(db, rs), **_money_meta()}


# ── Leaderboard (feedback 2026-11-10) ────────────────────────────────────────

@router.get("/leaderboard")
async def leaderboard(period: str = Query("lifetime", pattern="^(lifetime|monthly)$"),
                      month: Optional[str] = Query(None, max_length=7),
                      db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    data = build_leaderboard(db, period, month)
    users = {u.user_id: u for u in db.query(User).filter(
        User.user_id.in_([r["user_id"] for r in data["items"]])).all()} if data["items"] else {}
    for r in data["items"]:
        u = users.get(r["user_id"])
        r["username"], r["email"] = (u.username, u.email) if u else (None, None)
    data["months"] = available_months(db)
    return data


# ── Bonus settings (policy 2026-11-19) ───────────────────────────────────────

class BonusSettingsIn(BaseModel):
    tiers: List[List[float]]
    tier_max_usd: float
    video_min_views: int
    video_reward_usd: float
    video_max_per_month: int = 30
    monthly_cap_usd: float


@router.get("/bonus-settings")
async def get_bonus_settings(db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    return {**creator_bonus.get_config(db), "defaults": creator_bonus.DEFAULTS, "max_tiers": creator_bonus.MAX_TIERS}


@router.put("/bonus-settings")
async def put_bonus_settings(payload: BonusSettingsIn, db: Session = Depends(get_db),
                             admin: User = Depends(get_current_admin)):
    """New values apply to submissions sent from now on; earlier ones keep their amounts."""
    try:
        cfg = creator_bonus.save_config(db, payload.dict())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Cấu hình không hợp lệ: {e}")
    db.commit()
    return cfg
