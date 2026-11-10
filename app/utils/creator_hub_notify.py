"""Admin bell items for Creator Hub (2026-11): pending applications, channels and
views reconciliations. IDs follow the existing "type_id" format (one underscore)
so /admin/notifications/mark-read handles them unchanged."""
from datetime import timedelta

from app.models.models import CreatorApplication, CreatorChannel, CreatorReconciliation, User
from app.utils.creator_hub import PENDING_APP_STATUSES, OPEN_RECON_STATUSES, PLATFORMS
from app.utils.datetime_utils import get_vietnam_time


def creator_notifications(db, days: int, read_ids):
    since = get_vietnam_time().replace(tzinfo=None) - timedelta(days=days)
    read = set(read_ids or [])
    out = []

    def add(nid, title, message, ts):
        out.append({"id": nid, "type": "creator_hub", "title": title, "message": message,
                    "timestamp": ts, "is_read": nid in read})

    for a in (db.query(CreatorApplication)
              .filter(CreatorApplication.status.in_(PENDING_APP_STATUSES), CreatorApplication.created_at >= since)
              .order_by(CreatorApplication.created_at.desc()).limit(30).all()):
        add(f"creatorapp_{a.id}", "New creator application",
            f"{a.full_name} ({a.email or a.phone or '—'}) applied to the Creator Program", a.created_at)

    for c, u in (db.query(CreatorChannel, User).join(User, User.user_id == CreatorChannel.user_id)
                 .filter(CreatorChannel.status == "pending", CreatorChannel.created_at >= since)
                 .order_by(CreatorChannel.created_at.desc()).limit(30).all()):
        add(f"creatorch_{c.id}", "New creator channel",
            f"{u.username} registered {PLATFORMS.get(c.platform, c.platform)} channel “{c.channel_name}”", c.created_at)

    for r, u in (db.query(CreatorReconciliation, User).join(User, User.user_id == CreatorReconciliation.user_id)
                 .filter(CreatorReconciliation.status.in_(OPEN_RECON_STATUSES), CreatorReconciliation.submitted_at >= since)
                 .order_by(CreatorReconciliation.submitted_at.desc()).limit(30).all()):
        add(f"creatorrec_{r.id}", "Views submitted for review",
            f"{u.username} submitted views (est. ${r.estimated_usd:,.2f})", r.submitted_at)
    return out
