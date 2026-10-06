from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from app.database import get_db
from app.models.models import Announcement

router = APIRouter()


class PublicAnnouncement(BaseModel):
    announcement_id: int
    icon: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    link: Optional[str] = None
    is_important: bool
    created_at: Optional[str] = None


def _serialize(a: Announcement) -> PublicAnnouncement:
    return PublicAnnouncement(
        announcement_id=a.announcement_id,
        icon=a.icon,
        title=a.title,
        content=a.content,
        link=a.link,
        is_important=bool(a.is_important),
        created_at=a.created_at.isoformat() if a.created_at else None,
    )


@router.get("/announcements", response_model=List[PublicAnnouncement])
async def get_public_announcements(db: Session = Depends(get_db)):
    """Public (no-auth) list of active homepage announcements for the student
    landing page. Important items are pinned first, then manual order, then
    newest. Matches the public VIP-packages precedent (no auth dependency)."""
    items = (
        db.query(Announcement)
        .filter(Announcement.is_active == True)  # noqa: E712
        .order_by(
            Announcement.is_important.desc(),
            Announcement.display_order.asc(),
            Announcement.created_at.desc(),
        )
        .all()
    )
    return [_serialize(a) for a in items]


@router.get("/announcements/{announcement_id}", response_model=PublicAnnouncement)
async def get_public_announcement(announcement_id: int, db: Session = Depends(get_db)):
    """Public detail of a single active announcement (for the /news/{id}
    reading page)."""
    a = (
        db.query(Announcement)
        .filter(
            Announcement.announcement_id == announcement_id,
            Announcement.is_active == True,  # noqa: E712
        )
        .first()
    )
    if not a:
        raise HTTPException(status_code=404, detail="Announcement not found")
    return _serialize(a)
