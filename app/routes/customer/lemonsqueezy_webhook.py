"""
Lemon Squeezy Webhook Handler
Receives payment/subscription notifications from Lemon Squeezy.
Security: HMAC-SHA256 signature verification, idempotency checks.

Handled events:
- order_created: Log the order, create transaction record
- subscription_created: Create VIP subscription, activate VIP
- subscription_payment_success: Extend VIP (renewal payment)
- subscription_cancelled: Mark subscription as cancelling (grace period)
- subscription_expired: Deactivate VIP
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import (
    PackageTransaction, VIPSubscription, VIPPackage, User
)
from app.utils.lemonsqueezy_service import verify_webhook_signature
from app.utils.datetime_utils import get_vietnam_time
from datetime import timedelta
import json
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


def _extract_custom_data(event_data: dict) -> dict:
    """Extract custom_data from webhook event (meta.custom_data)."""
    meta = event_data.get("meta", {})
    return meta.get("custom_data", {})


def _extract_order_amount(attrs: dict) -> float:
    """Extract order total in dollars from attributes."""
    # Lemon Squeezy amounts are in cents
    total = attrs.get("total", 0)
    return total / 100.0


@router.post("/lemonsqueezy/webhook")
async def lemonsqueezy_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receive and process Lemon Squeezy webhooks.

    Security:
    1. HMAC-SHA256 signature verification
    2. Idempotency check (skip if already completed)
    """
    try:
        raw_body = await request.body()
        signature = request.headers.get("X-Signature", "")
        event_name = request.headers.get("X-Event-Name", "")

        # Step 1: Verify webhook signature
        if not verify_webhook_signature(raw_body, signature):
            logger.warning("Lemon Squeezy webhook signature verification failed")
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

        # Parse the webhook event
        event = json.loads(raw_body)
        event_data = event.get("data", {})
        attrs = event_data.get("attributes", {})

        logger.info(f"Lemon Squeezy webhook received: {event_name}")

        if event_name == "order_created":
            await _handle_order_created(event, event_data, attrs, db)

        elif event_name == "subscription_created":
            await _handle_subscription_created(event, event_data, attrs, db)

        elif event_name == "subscription_payment_success":
            await _handle_subscription_payment_success(event, event_data, attrs, db)

        elif event_name == "subscription_cancelled":
            await _handle_subscription_cancelled(event, event_data, attrs, db)

        elif event_name == "subscription_expired":
            await _handle_subscription_expired(event, event_data, attrs, db)

        else:
            logger.info(f"Unhandled Lemon Squeezy event: {event_name}")

        return {"status": "ok"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lemon Squeezy webhook error: {e}", exc_info=True)
        # Return 200 to prevent LS from retrying on our errors
        return {"status": "ok"}


async def _handle_order_created(event: dict, event_data: dict, attrs: dict, db: Session):
    """
    Handle order_created event.
    Creates a transaction record for tracking.
    """
    custom_data = _extract_custom_data(event)
    user_id = custom_data.get("user_id")
    package_id = custom_data.get("package_id")
    ls_order_id = str(event_data.get("id", ""))
    amount = _extract_order_amount(attrs)

    if not user_id or not package_id:
        logger.warning(f"LS order_created: missing custom_data. order_id={ls_order_id}")
        return

    # Idempotency check
    existing = db.query(PackageTransaction).filter(
        PackageTransaction.ls_order_id == ls_order_id,
        PackageTransaction.status == "completed"
    ).first()

    if existing:
        logger.info(f"LS order_created: already processed order {ls_order_id}")
        return

    # Check if transaction already exists (pending from checkout)
    existing_pending = db.query(PackageTransaction).filter(
        PackageTransaction.ls_order_id == ls_order_id
    ).first()

    if existing_pending:
        # Update existing pending transaction
        existing_pending.status = "completed"
        existing_pending.admin_note = f"Lemon Squeezy order confirmed"
        db.commit()
        logger.info(f"LS order_created: updated existing transaction for order {ls_order_id}")
        return

    logger.info(
        f"LS order_created: user_id={user_id}, package_id={package_id}, "
        f"order_id={ls_order_id}, amount=${amount:.2f}"
    )


async def _handle_subscription_created(event: dict, event_data: dict, attrs: dict, db: Session):
    """
    Handle subscription_created event.
    Creates VIP subscription and activates VIP for the user.
    """
    custom_data = _extract_custom_data(event)
    user_id = custom_data.get("user_id")
    package_id = custom_data.get("package_id")
    ls_subscription_id = str(event_data.get("id", ""))
    ls_customer_id = str(attrs.get("customer_id", ""))
    ls_order_id = str(attrs.get("order_id", ""))
    amount = _extract_order_amount(attrs)

    if not user_id or not package_id:
        logger.warning(
            f"LS subscription_created: missing custom_data. "
            f"subscription_id={ls_subscription_id}"
        )
        return

    user_id = int(user_id)
    package_id = int(package_id)

    # Idempotency check — skip if subscription already exists
    existing_sub = db.query(VIPSubscription).filter(
        VIPSubscription.ls_subscription_id == ls_subscription_id
    ).first()

    if existing_sub:
        logger.info(f"LS subscription_created: already processed {ls_subscription_id}")
        return

    # Get the package
    package = db.query(VIPPackage).filter(
        VIPPackage.package_id == package_id
    ).first()

    if not package:
        logger.error(f"LS subscription_created: package {package_id} not found")
        return

    # Subscription stacking logic
    if package.package_type == "single_skill":
        active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
            VIPSubscription.user_id == user_id,
            VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
            VIPSubscription.payment_status == "completed",
            ((VIPPackage.package_type == "single_skill") & (VIPPackage.skill_type == package.skill_type)) |
            (VIPPackage.package_type == "all_skills")
        ).order_by(VIPSubscription.end_date.desc()).first()
    else:
        active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
            VIPSubscription.user_id == user_id,
            VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
            VIPSubscription.payment_status == "completed",
            VIPPackage.package_type == "all_skills"
        ).order_by(VIPSubscription.end_date.desc()).first()

    if active_subscription:
        start_date = active_subscription.end_date
    else:
        start_date = get_vietnam_time().replace(tzinfo=None)

    end_date = start_date + timedelta(days=package.duration_months * 30)

    # Create subscription (completed immediately)
    subscription = VIPSubscription(
        user_id=user_id,
        package_id=package_id,
        start_date=start_date,
        end_date=end_date,
        payment_status="completed",
        ls_subscription_id=ls_subscription_id,
        ls_customer_id=ls_customer_id,
        is_auto_renew=True,
        created_at=get_vietnam_time().replace(tzinfo=None),
    )
    db.add(subscription)
    db.flush()

    # Create transaction record
    transaction = PackageTransaction(
        user_id=user_id,
        package_id=package_id,
        subscription_id=subscription.subscription_id,
        amount=amount if amount > 0 else package.price,
        payment_method="lemonsqueezy",
        ls_order_id=ls_order_id,
        status="completed",
        admin_note=f"Lemon Squeezy subscription: {ls_subscription_id}",
        created_at=get_vietnam_time().replace(tzinfo=None),
    )
    db.add(transaction)

    # Activate VIP on user
    user = db.query(User).filter(User.user_id == user_id).first()
    if user:
        user.is_vip = True
        if user.vip_expiry is None or end_date > user.vip_expiry:
            user.vip_expiry = end_date

    db.commit()

    logger.info(
        f"LS subscription_created SUCCESS: user_id={user_id}, "
        f"package={package.name}, ls_sub={ls_subscription_id}, "
        f"end_date={end_date}"
    )


async def _handle_subscription_payment_success(
    event: dict, event_data: dict, attrs: dict, db: Session
):
    """
    Handle subscription_payment_success (renewal payment).
    Extends VIP end_date by 1 month.
    """
    # subscription_payment_success has a Subscription Invoice object
    # We need to get the subscription_id from the attributes
    ls_subscription_id = str(attrs.get("subscription_id", ""))

    if not ls_subscription_id:
        logger.warning("LS subscription_payment_success: missing subscription_id")
        return

    # Find the subscription
    subscription = db.query(VIPSubscription).filter(
        VIPSubscription.ls_subscription_id == ls_subscription_id,
        VIPSubscription.payment_status == "completed",
    ).first()

    if not subscription:
        logger.warning(
            f"LS subscription_payment_success: subscription not found "
            f"for ls_sub={ls_subscription_id}"
        )
        return

    # Check if this is the initial payment (already handled by subscription_created)
    # billing_reason can be "initial" or "renewal"
    billing_reason = attrs.get("billing_reason", "renewal")
    if billing_reason == "initial":
        logger.info(
            f"LS subscription_payment_success: skipping initial payment "
            f"for ls_sub={ls_subscription_id} (handled by subscription_created)"
        )
        return

    # Extend VIP by the package duration
    package = subscription.package
    extension_days = package.duration_months * 30

    old_end = subscription.end_date
    new_end = subscription.end_date + timedelta(days=extension_days)
    subscription.end_date = new_end
    subscription.is_auto_renew = True
    subscription.cancelled_at = None  # Reset if was in grace period

    # Update user VIP expiry
    user = db.query(User).filter(User.user_id == subscription.user_id).first()
    if user:
        user.is_vip = True
        if user.vip_expiry is None or new_end > user.vip_expiry:
            user.vip_expiry = new_end

    # Create renewal transaction
    amount = _extract_order_amount(attrs) if _extract_order_amount(attrs) > 0 else package.price
    transaction = PackageTransaction(
        user_id=subscription.user_id,
        package_id=subscription.package_id,
        subscription_id=subscription.subscription_id,
        amount=amount,
        payment_method="lemonsqueezy",
        ls_order_id=str(attrs.get("order_id", "")),
        status="completed",
        admin_note=f"Auto-renewal: {old_end.strftime('%Y-%m-%d')} → {new_end.strftime('%Y-%m-%d')}",
        created_at=get_vietnam_time().replace(tzinfo=None),
    )
    db.add(transaction)

    db.commit()

    logger.info(
        f"LS subscription_payment_success: RENEWED user_id={subscription.user_id}, "
        f"ls_sub={ls_subscription_id}, new_end={new_end}"
    )


async def _handle_subscription_cancelled(
    event: dict, event_data: dict, attrs: dict, db: Session
):
    """
    Handle subscription_cancelled event.
    Marks the subscription as cancelling — VIP stays active until billing period ends.
    """
    ls_subscription_id = str(event_data.get("id", ""))
    ends_at = attrs.get("ends_at")  # When the subscription will actually expire

    subscription = db.query(VIPSubscription).filter(
        VIPSubscription.ls_subscription_id == ls_subscription_id
    ).first()

    if not subscription:
        logger.warning(
            f"LS subscription_cancelled: subscription not found "
            f"for ls_sub={ls_subscription_id}"
        )
        return

    subscription.is_auto_renew = False
    subscription.cancelled_at = get_vietnam_time().replace(tzinfo=None)

    # If ends_at is provided, update end_date to reflect when VIP actually expires
    if ends_at:
        from datetime import datetime
        try:
            # Parse ISO 8601 date
            end_dt = datetime.fromisoformat(ends_at.replace("Z", "+00:00")).replace(tzinfo=None)
            subscription.end_date = end_dt
        except (ValueError, TypeError):
            pass

    db.commit()

    logger.info(
        f"LS subscription_cancelled: user_id={subscription.user_id}, "
        f"ls_sub={ls_subscription_id}, ends_at={ends_at}"
    )


async def _handle_subscription_expired(
    event: dict, event_data: dict, attrs: dict, db: Session
):
    """
    Handle subscription_expired event.
    Deactivates VIP if no other active subscriptions exist.
    """
    ls_subscription_id = str(event_data.get("id", ""))

    subscription = db.query(VIPSubscription).filter(
        VIPSubscription.ls_subscription_id == ls_subscription_id
    ).first()

    if not subscription:
        logger.warning(
            f"LS subscription_expired: subscription not found "
            f"for ls_sub={ls_subscription_id}"
        )
        return

    subscription.payment_status = "expired"
    subscription.is_auto_renew = False

    # Check if user has OTHER active subscriptions
    other_active = db.query(VIPSubscription).filter(
        VIPSubscription.user_id == subscription.user_id,
        VIPSubscription.subscription_id != subscription.subscription_id,
        VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
        VIPSubscription.payment_status == "completed",
    ).first()

    user = db.query(User).filter(User.user_id == subscription.user_id).first()

    if user and not other_active:
        # No other active subscriptions — deactivate VIP
        user.is_vip = False
        logger.info(
            f"LS subscription_expired: VIP DEACTIVATED for user_id={subscription.user_id}"
        )
    elif user and other_active:
        # Has other active subs — update expiry to latest
        user.vip_expiry = other_active.end_date
        logger.info(
            f"LS subscription_expired: user_id={subscription.user_id} still has "
            f"active subscription until {other_active.end_date}"
        )

    db.commit()

    logger.info(
        f"LS subscription_expired: ls_sub={ls_subscription_id}, "
        f"user_id={subscription.user_id}"
    )
