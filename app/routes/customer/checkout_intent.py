"""POST /customer/checkout-intent — the payment page records that a signed-in user opened
checkout for a VIP package. Used only by the abandoned-checkout reminder email
(app/utils/email_automation.py). No payment logic here."""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import CheckoutIntent, User
from app.routes.admin.auth import get_current_student
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()


class IntentIn(BaseModel):
    package_id: Optional[int] = None


@router.post("/customer/checkout-intent")
async def record_checkout_intent(payload: IntentIn, db: Session = Depends(get_db),
                                 current: User = Depends(get_current_student)):
    now = get_vietnam_time().replace(tzinfo=None)
    recent = (db.query(CheckoutIntent.id)
              .filter(CheckoutIntent.user_id == current.user_id,
                      CheckoutIntent.created_at >= now - timedelta(minutes=30)).first())
    if not recent:   # reloads / back-and-forth within 30 min count once
        db.add(CheckoutIntent(user_id=current.user_id, package_id=payload.package_id, created_at=now))
        db.commit()
    return {"ok": True}
