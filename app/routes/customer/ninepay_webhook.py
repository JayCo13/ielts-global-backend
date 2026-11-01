"""
9Pay IPN Handler (international cards)
Receives server-to-server payment notifications from 9Pay and auto-completes transactions.
Security: SHA256 checksum verification, amount/currency validation, idempotency.

A 9Pay transaction is a PackageTransaction with payment_method="9pay" whose
transaction_code holds the invoice_no sent to 9Pay.
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import PackageTransaction, VIPSubscription, User
from app.routes.admin.auth import get_current_student
from app.utils import ninepay_service
from app.utils.datetime_utils import get_vietnam_time
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


def apply_ninepay_result(transaction: PackageTransaction, data: dict, db: Session, source: str) -> str:
    """Apply a verified 9Pay transaction result (from the IPN or the inquiry API).
    Idempotent: a completed or rejected transaction is never changed again.
    Returns the transaction status after applying."""
    if transaction.status != "pending":
        return transaction.status

    paid_amount = data.get("amount")
    if paid_amount is not None and int(float(paid_amount)) != int(transaction.amount):
        logger.warning(
            f"9Pay {source}: amount mismatch for invoice={transaction.transaction_code}. "
            f"Expected={int(transaction.amount)}, Got={paid_amount}"
        )
        return transaction.status
    currency = (data.get("currency") or "VND").upper()
    if currency != "VND":
        logger.warning(f"9Pay {source}: unexpected currency {currency} for invoice={transaction.transaction_code}")
        return transaction.status

    try:
        status_code = int(data.get("status"))
    except (TypeError, ValueError):
        return transaction.status

    subscription = db.query(VIPSubscription).filter(
        VIPSubscription.subscription_id == transaction.subscription_id
    ).first()
    payment_no = data.get("payment_no")

    if status_code == ninepay_service.STATUS_SUCCESS:
        transaction.status = "completed"
        transaction.admin_note = f"Auto-confirmed by 9Pay ({source}), payment_no={payment_no}, method={data.get('method') or '-'}"
        if subscription:
            subscription.payment_status = "completed"

        user = db.query(User).filter(User.user_id == transaction.user_id).first()
        if user and subscription:
            user.is_vip = True
            # Set vip_expiry to the latest end_date among all active subscriptions
            if user.vip_expiry is None or subscription.end_date > user.vip_expiry:
                user.vip_expiry = subscription.end_date

        db.commit()
        logger.info(
            f"9Pay payment SUCCESS ({source}): transaction_id={transaction.transaction_id}, "
            f"user_id={transaction.user_id}, amount={transaction.amount}, payment_no={payment_no}"
        )
    elif status_code in ninepay_service.FAILED_STATUSES:
        transaction.status = "reject"
        transaction.admin_note = (
            f"9Pay: payment failed (status={status_code}, "
            f"reason={data.get('failure_reason') or '-'}, payment_no={payment_no})"
        )
        if subscription and subscription.payment_status == "pending":
            subscription.payment_status = "reject"
        db.commit()
        logger.info(f"9Pay payment FAILED ({source}): transaction_id={transaction.transaction_id}, status={status_code}")
    # Any other status (processing / under review) leaves the transaction pending.

    return transaction.status


def _find_transaction(db: Session, invoice_no: str):
    return db.query(PackageTransaction).filter(
        PackageTransaction.payment_method == "9pay",
        PackageTransaction.transaction_code == invoice_no,
    ).first()


@router.post("/ninepay/ipn")
async def ninepay_ipn(request: Request, db: Session = Depends(get_db)):
    """
    Receive and process 9Pay IPNs (POST form: result, checksum, version).

    Security:
    1. Checksum verification: UPPER(SHA256(result + checksum_key)) must match
    2. Invoice must exist as a pending 9Pay transaction
    3. Amount and currency must match the DB
    4. Idempotency (already-processed transactions are skipped)
    """
    try:
        form = await request.form()
        result = form.get("result")
        checksum = form.get("checksum")

        if not ninepay_service.verify_checksum(result, checksum):
            logger.warning("9Pay IPN checksum verification failed")
            raise HTTPException(status_code=400, detail="Invalid checksum")

        data = ninepay_service.decode_result(result)
        invoice_no = data.get("invoice_no")
        if not invoice_no:
            raise HTTPException(status_code=400, detail="Missing invoice_no")

        transaction = _find_transaction(db, str(invoice_no))
        if not transaction:
            logger.warning(f"9Pay IPN: no transaction found for invoice={invoice_no}")
            raise HTTPException(status_code=404, detail="Transaction not found")

        apply_ninepay_result(transaction, data, db, source="IPN")
        return {"error": 0, "message": "ok"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"9Pay IPN error: {e}", exc_info=True)
        # Return 200 so 9Pay does not retry on our own errors
        return {"error": 0, "message": "ok"}


@router.get("/ninepay/verify/{invoice_no}", response_model=dict)
async def ninepay_verify(
    invoice_no: str,
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Status of the user's own 9Pay transaction. While it is still pending, asks the
    9Pay inquiry API directly, so a missed IPN does not leave a paid order pending."""
    transaction = _find_transaction(db, invoice_no)
    if not transaction or transaction.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if transaction.status == "pending":
        try:
            data = ninepay_service.inquire(invoice_no)
            if str(data.get("invoice_no", invoice_no)) == invoice_no:
                apply_ninepay_result(transaction, data, db, source="inquiry")
        except Exception as e:
            # Not created on 9Pay yet (customer still on the card form) or 9Pay unreachable
            logger.info(f"9Pay inquiry for invoice={invoice_no} not conclusive: {e}")

    return {
        "transaction_id": transaction.transaction_id,
        "status": transaction.status,
        "is_completed": transaction.status == "completed",
        "checked_at": get_vietnam_time().replace(tzinfo=None),
    }
