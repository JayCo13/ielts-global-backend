"""Creator bonus policy (feedback "ưu tiên 2 — chính sách affiliate quốc tế", 2026-11-19).

Three independent parts, all credited with the views submission (CreatorReconciliation)
they belong to, when the admin approves it:

1. Monthly Total Views Bonus per Channel — each channel on its own: the highest tier its
   views in the calendar month reach pays ONE bonus (tiers don't stack, channels are never
   added together), capped at `tier_max_usd` per channel per month. A second submission in
   the same month only pays the step up to the new tier.
2. Individual Video Bonus — links of videos that reached `video_min_views`; each link the
   admin approves pays `video_reward_usd`. A link can be submitted only once, ever.
3. Monthly Total Views Bonus Cap — the existing views reward (primary/secondary rate per
   1,000 views) is capped at `monthly_cap_usd` per creator per calendar month.

Everything is admin-tunable (system_settings, key prefix "creator_bonus."). Amounts are
computed with the settings in force when the creator submits and stored on the rows, so
a settings change only affects later submissions and approved rewards never change.
The creator UI does not show tiers, amounts or formulas (spec §4).
"""
import json
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func

from app.models.models import (
    CreatorReconciliation, CreatorReconciliationItem, CreatorVideoLink, SystemSetting,
)
from app.utils.creator_hub import normalize_channel_url, now

PREFIX = "creator_bonus."
DEFAULTS = {
    # [views threshold, USD]; "..." in the spec = the same $30 per 100,000 step up to 1M.
    "tiers": [[50000, 15], [100000, 30], [200000, 60], [300000, 90], [400000, 120], [500000, 150],
              [600000, 180], [700000, 210], [800000, 240], [900000, 270], [1000000, 300]],
    "tier_max_usd": 300.0,
    "video_min_views": 1000,
    "video_reward_usd": 1.0,
    "monthly_cap_usd": 600.0,
}
MAX_TIERS = 15            # keeps the JSON inside system_settings.setting_value (255 chars)
MAX_VIDEOS_PER_SUBMISSION = 200


# ── Settings ─────────────────────────────────────────────────────────────────

def _num(v, cast, default):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def get_config(db) -> dict:
    rows = {r.setting_key[len(PREFIX):]: r.setting_value for r in
            db.query(SystemSetting).filter(SystemSetting.setting_key.like(PREFIX + "%")).all()}
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    if rows.get("tiers"):
        try:
            cfg["tiers"] = clean_tiers(json.loads(rows["tiers"]))
        except (ValueError, TypeError):
            pass
    for key, cast in (("tier_max_usd", float), ("video_min_views", int),
                      ("video_reward_usd", float), ("monthly_cap_usd", float)):
        if rows.get(key) not in (None, ""):
            cfg[key] = _num(rows[key], cast, cfg[key])
    return cfg


def clean_tiers(raw) -> List[List]:
    """Sorted [[views, usd], ...] with positive views, non-negative amounts, no duplicates."""
    out = {}
    for t in raw or []:
        views = _num(t[0] if isinstance(t, (list, tuple)) else t.get("views"), int, 0)
        usd = _num(t[1] if isinstance(t, (list, tuple)) else t.get("usd"), float, -1)
        if views > 0 and usd >= 0:
            out[views] = round(usd, 2)
    return [[v, out[v]] for v in sorted(out)]


def save_config(db, cfg: dict) -> dict:
    """Validate and store; returns the stored config. Does not commit."""
    tiers = clean_tiers(cfg.get("tiers"))
    if len(tiers) > MAX_TIERS:
        raise ValueError(f"At most {MAX_TIERS} tiers.")
    values = {
        "tiers": json.dumps(tiers, separators=(",", ":")),
        "tier_max_usd": _num(cfg.get("tier_max_usd"), float, -1),
        "video_min_views": _num(cfg.get("video_min_views"), int, -1),
        "video_reward_usd": _num(cfg.get("video_reward_usd"), float, -1),
        "monthly_cap_usd": _num(cfg.get("monthly_cap_usd"), float, -1),
    }
    for k in ("tier_max_usd", "video_min_views", "video_reward_usd", "monthly_cap_usd"):
        if values[k] < 0:
            raise ValueError(f"Invalid value for {k}.")
    if len(values["tiers"]) > 255:
        raise ValueError("Too many tiers.")
    ts = now()
    for k, v in values.items():
        key = PREFIX + k
        row = db.query(SystemSetting).filter(SystemSetting.setting_key == key).first()
        if not row:
            row = SystemSetting(setting_key=key)
            db.add(row)
        row.setting_value = str(v)
        row.updated_at = ts
    return get_config_from_values(values)


def get_config_from_values(values: dict) -> dict:
    return {"tiers": json.loads(values["tiers"]), "tier_max_usd": float(values["tier_max_usd"]),
            "video_min_views": int(values["video_min_views"]),
            "video_reward_usd": float(values["video_reward_usd"]),
            "monthly_cap_usd": float(values["monthly_cap_usd"])}


# ── Calculations ─────────────────────────────────────────────────────────────

def tier_amount(views: int, cfg: dict) -> float:
    """Bonus of the highest tier reached (not cumulative), capped per channel."""
    best = 0.0
    for threshold, usd in cfg["tiers"]:
        if views >= threshold:
            best = max(best, float(usd))
    return round(min(best, float(cfg["tier_max_usd"])), 2)


def month_range(ts: datetime) -> Tuple[datetime, datetime]:
    start = ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def _month_recons(db, user_id: int, ts: datetime):
    start, end = month_range(ts)
    return (db.query(CreatorReconciliation)
            .filter(CreatorReconciliation.user_id == user_id, CreatorReconciliation.status != "rejected",
                    CreatorReconciliation.submitted_at >= start, CreatorReconciliation.submitted_at < end)
            .all())


def month_state(db, user_id: int, ts: datetime) -> dict:
    """What the creator already has this month (non-rejected submissions):
    views reward used against the cap, and per channel {views, tier bonus}."""
    recons = _month_recons(db, user_id, ts)
    used = 0.0
    for r in recons:
        used += float(r.views_reward_usd if r.views_reward_usd is not None else (r.estimated_usd or 0))
    channels: Dict[int, dict] = {}
    ids = [r.id for r in recons]
    if ids:
        for it in db.query(CreatorReconciliationItem).filter(
                CreatorReconciliationItem.reconciliation_id.in_(ids)).all():
            c = channels.setdefault(it.channel_id, {"views": 0, "bonus": 0.0})
            c["views"] += int(it.period_views or 0)
            c["bonus"] += float(it.tier_bonus_usd or 0)
    return {"views_reward_used": round(used, 2), "channels": channels}


def price_submission(db, user_id: int, ts: datetime, lines: List[dict], video_count: int, cfg: dict) -> dict:
    """lines: [{"channel_id", "period_views", "views_usd"}]. Adds "tier_views"/"tier_bonus"
    to each line and returns the submission totals."""
    state = month_state(db, user_id, ts)
    raw = round(sum(float(l["views_usd"]) for l in lines), 2)
    room = max(0.0, float(cfg["monthly_cap_usd"]) - state["views_reward_used"])
    views_usd = round(min(raw, room), 2)
    tier_total = 0.0
    for l in lines:
        prev = state["channels"].get(l["channel_id"], {"views": 0, "bonus": 0.0})
        month_views = prev["views"] + max(0, int(l["period_views"]))
        bonus = max(0.0, round(tier_amount(month_views, cfg) - prev["bonus"], 2))
        l["tier_views"], l["tier_bonus"] = month_views, bonus
        tier_total += bonus
    video_usd = round(float(cfg["video_reward_usd"]), 2)
    return {
        "views_reward_raw_usd": raw, "views_reward_usd": views_usd,
        "tier_bonus_usd": round(tier_total, 2), "video_reward_usd": video_usd,
        "estimated_usd": round(views_usd + tier_total + video_usd * max(0, video_count), 2),
    }


# ── Video links ──────────────────────────────────────────────────────────────

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def video_key(url: str) -> str:
    """Normalised video URL for the once-ever duplicate check (same video however pasted)."""
    u = (url or "").strip()
    if not re.match(r"^[a-zA-Z]+://", u):
        u = "https://" + u
    base = normalize_channel_url(u)
    path, _, query = base.partition("?")
    params = dict(kv.split("=", 1) if "=" in kv else (kv, "") for kv in query.split("&") if kv)
    host = path.split("/", 1)[0]
    rest = path.split("/", 1)[1] if "/" in path else ""
    vid = None
    if host == "youtu.be":
        vid = rest.split("/")[0]
    elif host.endswith("youtube.com"):
        if rest.startswith("watch"):
            vid = params.get("v")
        elif rest.startswith(("shorts/", "live/", "embed/")):
            vid = rest.split("/")[1] if len(rest.split("/")) > 1 else None
    if vid and _YT_ID.match(vid):
        return "youtube:" + vid.lower()
    if "tiktok.com" in host or "instagram.com" in host or "facebook.com" in host:
        return path[:500]           # their video ids live in the path; queries are share noise
    return base[:500]


def clean_video_urls(urls: List[str]) -> List[Tuple[str, str]]:
    """[(url, key)] — trimmed, validated, de-duplicated within the list (raises ValueError)."""
    out, seen = [], set()
    for raw in urls or []:
        u = (raw or "").strip()
        if not u:
            continue
        if len(u) > 500 or "." not in u or " " in u:
            raise ValueError(f"“{u[:60]}” is not a valid video link.")
        if not re.match(r"^https?://", u, re.I):
            u = "https://" + u
        key = video_key(u)
        if key in seen:
            raise ValueError("This video has already been submitted.")
        seen.add(key)
        out.append((u, key))
    if len(out) > MAX_VIDEOS_PER_SUBMISSION:
        raise ValueError(f"At most {MAX_VIDEOS_PER_SUBMISSION} videos per submission.")
    return out


def taken_keys(db, keys: List[str]) -> set:
    if not keys:
        return set()
    return {k for (k,) in db.query(CreatorVideoLink.url_key).filter(CreatorVideoLink.url_key.in_(keys)).all()}


def release_videos(db, reconciliation_id: int):
    """Whole submission rejected: its pending links are returned (they paid nothing), so
    the creator can send them again with the corrected submission. Does not commit."""
    (db.query(CreatorVideoLink)
     .filter(CreatorVideoLink.reconciliation_id == reconciliation_id, CreatorVideoLink.status == "pending")
     .update({"status": "returned", "url_key": None, "reviewed_at": now()}, synchronize_session=False))


def video_rows(db, reconciliation_ids: List[int]) -> Dict[int, list]:
    out: Dict[int, list] = {}
    if reconciliation_ids:
        for v in (db.query(CreatorVideoLink)
                  .filter(CreatorVideoLink.reconciliation_id.in_(reconciliation_ids))
                  .order_by(CreatorVideoLink.id).all()):
            out.setdefault(v.reconciliation_id, []).append(v)
    return out


def approved_count(db, reconciliation_id: int) -> int:
    return db.query(func.count(CreatorVideoLink.id)).filter(
        CreatorVideoLink.reconciliation_id == reconciliation_id,
        CreatorVideoLink.status == "approved").scalar() or 0
