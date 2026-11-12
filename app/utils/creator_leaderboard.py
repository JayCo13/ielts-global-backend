"""Creator Leaderboard (feedback 2026-11-10, spec "CREATOR LEADERBOARD").

Rows = every creator with Creator Hub access (Monthly: only those with activity that month).
Columns: total views, views reward, affiliate commission, total reward (= views reward +
commission), sign-ups / views and VIP buyers / views.

Official rank (spec §3): VIP/views desc -> sign-ups/views desc -> total views desc.
Column sorting is done in the UI and never changes `rank`.

Everything is computed from immutable, timestamped rows, so a past month always gives the
same result (spec §5.2 "kept for review") without a snapshot table:
  * views + views reward: approved reconciliations, by approval time (reviewed_at) — the
    moment the reward was credited to the wallet;
  * commission + VIP buyers: affiliate_wallet_tx 'commission' rows, by created_at;
  * sign-ups: accounts with referred_by = creator, by their created_at.
"""
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import func

from app.models.models import (
    User, CreatorProfile, CreatorReconciliation, CreatorReconciliationItem, AffiliateWalletTx, CreatorChannel,
)
from app.utils.affiliate import AFFILIATE_CURRENCY, UNIT_DIVISOR
from app.utils.creator_hub import now, platform_label


def month_bounds(month: Optional[str]) -> Tuple[datetime, datetime, str]:
    """'YYYY-MM' -> [start, end) as Vietnam-naive datetimes. Defaults to the current month."""
    cur = now()
    y, m = cur.year, cur.month
    if month:
        try:
            y, m = (int(x) for x in month.split("-", 1))
            if not (1 <= m <= 12 and 2000 <= y <= 2100):
                raise ValueError
        except ValueError:
            y, m = cur.year, cur.month
    start = datetime(y, m, 1)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return start, end, f"{y:04d}-{m:02d}"


def available_months(db):
    """'YYYY-MM' from the first creator activity up to the current month, newest first."""
    firsts = [
        db.query(func.min(CreatorReconciliation.reviewed_at)).filter(CreatorReconciliation.status == "approved").scalar(),
        db.query(func.min(AffiliateWalletTx.created_at)).filter(AffiliateWalletTx.type == "commission").scalar(),
        db.query(func.min(CreatorProfile.enabled_at)).scalar(),
    ]
    firsts = [d for d in firsts if d]
    cur = now()
    first = min(firsts) if firsts else cur
    out, y, m = [], cur.year, cur.month
    while (y, m) >= (first.year, first.month) and len(out) < 120:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def _rate(n: int, views: int) -> float:
    return (n * 100.0 / views) if views > 0 else 0.0


def build_leaderboard(db, period: str = "lifetime", month: Optional[str] = None):
    monthly = period == "monthly"
    start = end = None
    month_key = None
    if monthly:
        start, end, month_key = month_bounds(month)

    def win(col):
        return [col >= start, col < end] if monthly else []

    profiles = db.query(CreatorProfile).filter(CreatorProfile.hub_access == True).all()  # noqa: E712
    ids = [p.user_id for p in profiles]
    if not ids:
        return {"period": "monthly" if monthly else "lifetime", "month": month_key, "items": [],
                "currency": AFFILIATE_CURRENCY, "unit_divisor": UNIT_DIVISOR}

    views = dict(db.query(CreatorReconciliation.user_id, func.coalesce(func.sum(CreatorReconciliationItem.period_views), 0))
                 .join(CreatorReconciliationItem, CreatorReconciliationItem.reconciliation_id == CreatorReconciliation.id)
                 .filter(CreatorReconciliation.status == "approved", CreatorReconciliation.user_id.in_(ids),
                         *win(CreatorReconciliation.reviewed_at))
                 .group_by(CreatorReconciliation.user_id).all())
    reward = dict(db.query(CreatorReconciliation.user_id, func.coalesce(func.sum(CreatorReconciliation.approved_units), 0))
                  .filter(CreatorReconciliation.status == "approved", CreatorReconciliation.user_id.in_(ids),
                          *win(CreatorReconciliation.reviewed_at))
                  .group_by(CreatorReconciliation.user_id).all())
    commission = dict(db.query(AffiliateWalletTx.user_id, func.coalesce(func.sum(AffiliateWalletTx.amount), 0))
                      .filter(AffiliateWalletTx.type == "commission", AffiliateWalletTx.user_id.in_(ids),
                              *win(AffiliateWalletTx.created_at))
                      .group_by(AffiliateWalletTx.user_id).all())
    vip = dict(db.query(AffiliateWalletTx.user_id, func.count(func.distinct(AffiliateWalletTx.source_user_id)))
               .filter(AffiliateWalletTx.type == "commission", AffiliateWalletTx.source_user_id.isnot(None),
                       AffiliateWalletTx.user_id.in_(ids), *win(AffiliateWalletTx.created_at))
               .group_by(AffiliateWalletTx.user_id).all())
    signups = dict(db.query(User.referred_by, func.count(User.user_id))
                   .filter(User.referred_by.in_(ids), *win(User.created_at))
                   .group_by(User.referred_by).all())
    users = {u.user_id: u for u in db.query(User).filter(User.user_id.in_(ids)).all()}
    # Approved channels of each creator (feedback 2026-11-11: "Channels" column, same in both tabs).
    chans = {}
    for c in (db.query(CreatorChannel)
              .filter(CreatorChannel.user_id.in_(ids), CreatorChannel.status == "approved")
              .order_by(CreatorChannel.approved_total_views.desc(), CreatorChannel.id.asc()).all()):
        chans.setdefault(c.user_id, []).append({
            "channel_name": c.channel_name, "platform": c.platform, "platform_label": platform_label(c),
            "total_views": int(c.approved_total_views or 0), "channel_url": c.channel_url,
        })

    rows = []
    for p in profiles:
        uid = p.user_id
        v, s, b = int(views.get(uid, 0) or 0), int(signups.get(uid, 0) or 0), int(vip.get(uid, 0) or 0)
        rw, cm = int(reward.get(uid, 0) or 0), int(commission.get(uid, 0) or 0)
        if monthly and not (v or s or b or rw or cm):
            continue
        u = users.get(uid)
        rows.append({
            "user_id": uid,
            "name": (p.full_name or (u.username if u else None) or f"Creator {uid}").strip(),
            "total_views": v, "views_reward": rw, "affiliate_commission": cm, "total_reward": rw + cm,
            "signups": s, "vip_buyers": b,
            "channels": chans.get(uid, []), "channel_count": len(chans.get(uid, [])),
            "signup_rate": round(_rate(s, v), 4), "vip_rate": round(_rate(b, v), 4),
            "_sr": _rate(s, v), "_vr": _rate(b, v),
        })
    rows.sort(key=lambda r: (-r["_vr"], -r["_sr"], -r["total_views"], r["name"].lower()))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r.pop("_sr"); r.pop("_vr")
    return {"period": "monthly" if monthly else "lifetime", "month": month_key, "items": rows,
            "currency": AFFILIATE_CURRENCY, "unit_divisor": UNIT_DIVISOR}


def abbreviate_name(name: str) -> str:
    """Privacy mask for the student-facing leaderboard.
    'Nguyen Van An' -> 'Nguyen V. A.'; single word 'taicopgm' -> 'ta***'.
    Emails are reduced to their local part before masking."""
    name = (name or "").strip().split("@")[0].strip()
    parts = [p for p in name.split() if p]
    if not parts:
        return "Creator"
    if len(parts) == 1:
        w = parts[0]
        return (w[:2] if len(w) > 3 else w[:1]) + "***"
    return parts[0] + " " + " ".join(p[0].upper() + "." for p in parts[1:])
