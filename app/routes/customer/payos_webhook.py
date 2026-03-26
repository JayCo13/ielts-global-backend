"""
PayOS Webhook Handler
Receives payment notifications from PayOS and auto-completes transactions.
Security: HMAC_SHA256 checksum verification, amount validation, idempotency.
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import PackageTransaction, VIPSubscription, User
from app.utils.payos_service import verify_webhook
from app.utils.datetime_utils import get_vietnam_time
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/payos/webhook")
async def payos_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receive and process PayOS payment webhooks.
    
    Security:
    1. HMAC_SHA256 checksum verification (via PayOS SDK)
    2. Idempotency check (skip if already completed)
    3. Amount validation (webhook amount must match DB)
    4. Order code validation (must exist in DB)
    """
    try:
        raw_body = await request.body()
        
        # Step 1: Verify webhook signature (HMAC_SHA256)
        # This will raise WebhookError if checksum is invalid
        try:
            webhook_data = verify_webhook(raw_body)
        except Exception as e:
            logger.warning(f"PayOS webhook verification failed: {e}")
            raise HTTPException(status_code=400, detail="Invalid webhook signature")
        
        # Extract payment info from verified webhook data
        # PayOS webhook data structure: { code, desc, data: { orderCode, amount, ... } }
        if hasattr(webhook_data, 'data'):
            payment_data = webhook_data.data
        else:
            payment_data = webhook_data
            
        order_code = getattr(payment_data, 'order_code', None) or getattr(payment_data, 'orderCode', None)
        webhook_amount = getattr(payment_data, 'amount', None)
        
        # Handle PayOS test/confirmation webhook (code "00" with orderCode 123)
        if order_code and int(order_code) == 123:
            logger.info("PayOS test webhook received - responding with success")
            return {"error": 0, "message": "ok"}
        
        if not order_code:
            logger.warning("PayOS webhook missing orderCode")
            raise HTTPException(status_code=400, detail="Missing orderCode")
        
        # Step 2: Find the transaction by order code
        transaction = db.query(PackageTransaction).filter(
            PackageTransaction.payos_order_code == int(order_code)
        ).first()
        
        if not transaction:
            logger.warning(f"PayOS webhook: no transaction found for orderCode={order_code}")
            raise HTTPException(status_code=404, detail="Transaction not found")
        
        # Step 3: Idempotency - skip if already processed
        if transaction.status == "completed":
            logger.info(f"PayOS webhook: transaction {transaction.transaction_id} already completed, skipping")
            return {"error": 0, "message": "ok"}
        
        # Step 4: Amount validation
        if webhook_amount is not None and int(webhook_amount) != int(transaction.amount):
            logger.warning(
                f"PayOS webhook: amount mismatch for orderCode={order_code}. "
                f"Expected={int(transaction.amount)}, Got={int(webhook_amount)}"
            )
            raise HTTPException(status_code=400, detail="Amount mismatch")
        
        # Determine payment status from webhook
        webhook_code = getattr(webhook_data, 'code', None)
        is_success = str(webhook_code) == "00"
        
        if is_success:
            # Payment successful - activate subscription
            transaction.status = "completed"
            transaction.admin_note = "Tự động xác nhận bởi PayOS"
            
            # Update subscription
            subscription = db.query(VIPSubscription).filter(
                VIPSubscription.subscription_id == transaction.subscription_id
            ).first()
            
            if subscription:
                subscription.payment_status = "completed"
            
            # Update user VIP status
            user = db.query(User).filter(
                User.user_id == transaction.user_id
            ).first()
            
            if user and subscription:
                user.is_vip = True
                # Set vip_expiry to the latest end_date among all active subscriptions
                if user.vip_expiry is None or subscription.end_date > user.vip_expiry:
                    user.vip_expiry = subscription.end_date
            
            db.commit()
            logger.info(
                f"PayOS payment SUCCESS: transaction_id={transaction.transaction_id}, "
                f"user_id={transaction.user_id}, amount={transaction.amount}"
            )
        else:
            # Payment failed or cancelled
            transaction.status = "reject"
            transaction.admin_note = f"PayOS: thanh toán thất bại (code={webhook_code})"
            
            subscription = db.query(VIPSubscription).filter(
                VIPSubscription.subscription_id == transaction.subscription_id
            ).first()
            
            if subscription:
                subscription.payment_status = "reject"
            
            db.commit()
            logger.info(
                f"PayOS payment FAILED: transaction_id={transaction.transaction_id}, "
                f"code={webhook_code}"
            )
        
        return {"error": 0, "message": "ok"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PayOS webhook error: {e}", exc_info=True)
        # Always return 200 to PayOS to prevent retries on our errors
        return {"error": 0, "message": "ok"}
