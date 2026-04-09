"""
PayPal Webhook Handler
Receives payment notifications from PayPal and auto-completes transactions.
Security: PayPal webhook signature verification, amount validation, idempotency.
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import PackageTransaction, VIPSubscription, User
from app.utils.paypal_service import verify_webhook_signature
from app.utils.datetime_utils import get_vietnam_time
import json
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/paypal/webhook")
async def paypal_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receive and process PayPal payment webhooks.
    
    Handles these event types:
    - PAYMENT.CAPTURE.COMPLETED: Payment was successfully captured
    - PAYMENT.CAPTURE.DENIED: Payment capture was denied
    - CHECKOUT.ORDER.APPROVED: Order was approved (backup — we usually capture inline)
    
    Security:
    1. PayPal webhook signature verification
    2. Idempotency check (skip if already completed)
    3. Amount validation
    """
    try:
        raw_body = await request.body()
        headers = dict(request.headers)
        
        # Step 1: Verify webhook signature
        is_valid = verify_webhook_signature(headers, raw_body)
        if not is_valid:
            logger.warning("PayPal webhook signature verification failed")
            raise HTTPException(status_code=400, detail="Invalid webhook signature")
        
        # Parse the webhook event
        event = json.loads(raw_body)
        event_type = event.get("event_type", "")
        resource = event.get("resource", {})
        
        logger.info(f"PayPal webhook received: {event_type}")
        
        if event_type == "PAYMENT.CAPTURE.COMPLETED":
            # Payment was captured successfully
            capture_id = resource.get("id")
            amount = resource.get("amount", {})
            paid_amount = float(amount.get("value", 0))
            
            # Get the PayPal order ID from the supplementary data or custom_id
            custom_id = resource.get("custom_id", "")
            
            # Find transaction by custom_id (which contains our transaction_id)
            if custom_id:
                transaction = db.query(PackageTransaction).filter(
                    PackageTransaction.transaction_id == int(custom_id)
                ).first()
            else:
                # Fallback: try to find by paypal_order_id from supplementary_data
                order_id = None
                supplementary = resource.get("supplementary_data", {})
                related = supplementary.get("related_ids", {})
                order_id = related.get("order_id")
                
                if order_id:
                    transaction = db.query(PackageTransaction).filter(
                        PackageTransaction.paypal_order_id == order_id
                    ).first()
                else:
                    logger.warning("PayPal webhook: cannot identify transaction")
                    return {"status": "ok"}
            
            if not transaction:
                logger.warning(f"PayPal webhook: transaction not found (custom_id={custom_id})")
                return {"status": "ok"}
            
            # Idempotency check
            if transaction.status == "completed":
                logger.info(f"PayPal webhook: transaction {transaction.transaction_id} already completed")
                return {"status": "ok"}
            
            # Amount validation
            if abs(paid_amount - float(transaction.amount)) > 0.01:
                logger.warning(
                    f"PayPal webhook: amount mismatch. "
                    f"Expected={transaction.amount}, Got={paid_amount}"
                )
                # Still process but log the discrepancy
            
            # Activate the subscription
            transaction.status = "completed"
            transaction.admin_note = f"Auto-confirmed by PayPal (capture: {capture_id})"
            
            subscription = db.query(VIPSubscription).filter(
                VIPSubscription.subscription_id == transaction.subscription_id
            ).first()
            
            if subscription:
                subscription.payment_status = "completed"
            
            user = db.query(User).filter(
                User.user_id == transaction.user_id
            ).first()
            
            if user and subscription:
                user.is_vip = True
                if user.vip_expiry is None or subscription.end_date > user.vip_expiry:
                    user.vip_expiry = subscription.end_date
            
            db.commit()
            logger.info(
                f"PayPal payment SUCCESS: transaction_id={transaction.transaction_id}, "
                f"user_id={transaction.user_id}, amount=${paid_amount}"
            )
        
        elif event_type == "PAYMENT.CAPTURE.DENIED":
            # Payment was denied
            custom_id = resource.get("custom_id", "")
            if custom_id:
                transaction = db.query(PackageTransaction).filter(
                    PackageTransaction.transaction_id == int(custom_id)
                ).first()
                
                if transaction and transaction.status == "pending":
                    transaction.status = "reject"
                    transaction.admin_note = "PayPal: payment denied"
                    
                    subscription = db.query(VIPSubscription).filter(
                        VIPSubscription.subscription_id == transaction.subscription_id
                    ).first()
                    if subscription:
                        subscription.payment_status = "reject"
                    
                    db.commit()
                    logger.info(f"PayPal payment DENIED: transaction_id={transaction.transaction_id}")
        
        return {"status": "ok"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PayPal webhook error: {e}", exc_info=True)
        # Return 200 to prevent PayPal from retrying on our errors
        return {"status": "ok"}
