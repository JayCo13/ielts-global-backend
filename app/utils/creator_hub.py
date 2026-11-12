"""Creator Hub helpers (2026-11).

Creator Hub = Creator Program + Affiliate Program, behind ONE access flag
(CreatorProfile.hub_access). Rules from the spec "Creator Program – EnglishOnComputer":

  * Every account starts with access Disabled. Approving a Creator Application, or an
    admin "direct grant", enables it. Disabling hides the Hub but never deletes data,
    wallet balance or history.
  * Channels are approved one by one; only approved channels may submit views.
  * Reward: primary channel $0.30 / 1,000 views, secondary $0.10 / 1,000 views.
    A creator with one approved channel is always paid at the primary rate.
  * Rewards are credited to the shared affiliate wallet (AffiliateWalletTx
    'creator_reward') only when the admin approves a reconciliation, exactly once.
"""
import os
import re
from typing import Optional

from app.models.models import CreatorProfile, CreatorAccessLog, CreatorApplication, User
from app.utils.affiliate import AFFILIATE_CURRENCY, UNIT_DIVISOR
from app.utils.currency import USD_VND_RATE
from app.utils.datetime_utils import get_vietnam_time

PRIMARY_RATE_PER_K = float(os.getenv("CREATOR_PRIMARY_RATE_PER_K", "0.30"))
SECONDARY_RATE_PER_K = float(os.getenv("CREATOR_SECONDARY_RATE_PER_K", "0.10"))

PLATFORMS = {
    "youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram",
    "facebook": "Facebook", "x": "X (Twitter)", "threads": "Threads",
    "other": "Others",   # keep last: creator types the platform name (feedback 2026-11-11)
}
# Two views submissions must be at least this many days apart (anti-spam, feedback 2026-11-10).
# Rejected submissions don't count, so a creator can correct and resubmit straight away.
SUBMIT_MIN_DAYS = int(os.getenv("CREATOR_SUBMIT_MIN_DAYS", "20"))
CHANNEL_STATUSES = ("pending", "approved", "rejected", "suspended")
PENDING_APP_STATUSES = ("new", "contacted")      # shown as "Pending Approval"
OPEN_RECON_STATUSES = ("submitted", "under_review")


def now():
    return get_vietnam_time().replace(tzinfo=None)


def app_status_label(status: Optional[str]) -> str:
    """Spec status of an application row: pending / approved / rejected."""
    if not status:
        return "not_applied"
    return "pending" if status in PENDING_APP_STATUSES else status


def get_profile(db, user_id: int) -> Optional[CreatorProfile]:
    return db.query(CreatorProfile).filter(CreatorProfile.user_id == user_id).first()


def has_hub_access(db, user_id: int) -> bool:
    p = get_profile(db, user_id)
    return bool(p and p.hub_access)


def referral_allowed(db, user_id: int) -> bool:
    """A referral link only counts (clicks / sign-up attribution) when its owner has
    Creator Hub access. CREATOR_HUB_GATE=0 switches the check off."""
    if os.getenv("CREATOR_HUB_GATE", "1") == "0":
        return True
    return has_hub_access(db, user_id)


def latest_application(db, user: User) -> Optional[CreatorApplication]:
    """The account's most recent application (linked by user_id)."""
    return (db.query(CreatorApplication).filter(CreatorApplication.user_id == user.user_id)
            .order_by(CreatorApplication.created_at.desc(), CreatorApplication.id.desc()).first())


def set_hub_access(db, user: User, enabled: bool, source: str, actor_id: Optional[int] = None,
                   note: Optional[str] = None, application: Optional[CreatorApplication] = None) -> CreatorProfile:
    """Enable/disable Creator Hub for `user`, creating the profile if needed and
    logging the change. Does not commit."""
    p = get_profile(db, user.user_id)
    ts = now()
    if not p:
        p = CreatorProfile(user_id=user.user_id, hub_access=False, created_at=ts, updated_at=ts,
                           email=user.email)
        db.add(p)
    if application is not None:
        p.full_name = application.full_name or p.full_name
        p.phone = application.phone or p.phone
        p.email = application.email or p.email or user.email
        p.country_code = (application.country_code if application.country_code not in (None, "OTHER")
                          else p.country_code)
    if bool(p.hub_access) != bool(enabled):
        p.hub_access = bool(enabled)
        if enabled:
            p.access_source = source
            p.enabled_at = ts
        db.add(CreatorAccessLog(user_id=user.user_id, action="enable" if enabled else "disable",
                                source=source, note=note, actor_id=actor_id, created_at=ts))
    p.updated_at = ts
    return p


def usd_to_units(usd: float) -> int:
    """USD amount -> wallet minor units (USD wallet: cents; VND wallet: VND)."""
    if AFFILIATE_CURRENCY == "USD":
        return int(round(usd * 100))
    if AFFILIATE_CURRENCY == "VND":
        return int(round(usd * USD_VND_RATE))
    return int(round(usd * UNIT_DIVISOR))


def reward_usd(views: int, channel_type: str) -> float:
    rate = PRIMARY_RATE_PER_K if channel_type == "primary" else SECONDARY_RATE_PER_K
    return round(max(0, int(views or 0)) * rate / 1000.0, 2)


_TRACKING = re.compile(r"^(utm_|si$|igsh|is_from_webapp|sender_device|feature$|ref$|fbclid|mibextid)")


def normalize_channel_url(url: str) -> str:
    """Lower-cased host+path without scheme, 'www.'/'m.', trailing slash or tracking
    params — so the same channel is detected as a duplicate however it was pasted."""
    u = (url or "").strip()
    u = re.sub(r"^[a-zA-Z]+://", "", u)
    u = u.split("#", 1)[0]
    path, _, query = u.partition("?")
    path = re.sub(r"^(www\.|m\.|mobile\.|vm\.)", "", path.lower()).rstrip("/")
    keep = [kv for kv in query.split("&") if kv and not _TRACKING.match(kv.split("=", 1)[0].lower())]
    return path + (("?" + "&".join(sorted(keep))) if keep else "")


def platform_label(c) -> str:
    """Display name of a channel's platform; 'other' shows the creator-typed name."""
    if c.platform == "other" and (getattr(c, "platform_name", None) or "").strip():
        return c.platform_name.strip()
    return PLATFORMS.get(c.platform, c.platform)


def detect_platform(url: str) -> Optional[str]:
    u = (url or "").lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "tiktok.com" in u:
        return "tiktok"
    if "instagram.com" in u:
        return "instagram"
    if "threads.net" in u or "threads.com" in u:
        return "threads"
    if "facebook.com" in u or "fb.com" in u or "fb.watch" in u:
        return "facebook"
    if re.search(r"(^|//|\.)(x|twitter)\.com", u):
        return "x"
    return None


def last_counted_submission(db, user_id: int):
    """Latest non-rejected views submission of `user_id` (None if there is none)."""
    from app.models.models import CreatorReconciliation
    return (db.query(CreatorReconciliation)
            .filter(CreatorReconciliation.user_id == user_id, CreatorReconciliation.status != "rejected")
            .order_by(CreatorReconciliation.submitted_at.desc(), CreatorReconciliation.id.desc()).first())


def next_submit_at(db, user_id: int):
    """Earliest time the creator may submit views again, or None when allowed now."""
    from datetime import timedelta
    last = last_counted_submission(db, user_id)
    if not last or not last.submitted_at or SUBMIT_MIN_DAYS <= 0:
        return None
    nxt = last.submitted_at + timedelta(days=SUBMIT_MIN_DAYS)
    return nxt if nxt > now() else None
