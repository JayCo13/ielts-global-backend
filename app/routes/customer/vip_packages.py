from fastapi import APIRouter, Depends, HTTPException, status, Form, File, UploadFile, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import VIPPackage, VIPSubscription, User, PackageTransaction
from app.routes.admin.auth import get_current_student
from datetime import datetime, timedelta
from typing import List
from pydantic import BaseModel
import os
from uuid import uuid4
from app.utils.datetime_utils import get_vietnam_time
import logging
logger = logging.getLogger(__name__)
router = APIRouter()

class PackageResponse(BaseModel):
    package_id: int
    name: str
    duration_months: int
    price: float
    description: str | None
    package_type: str
    skill_type: str | None

@router.get("/packages/available", response_model=List[PackageResponse])
async def get_available_packages(
    db: Session = Depends(get_db)
):
    """Get all available VIP packages (public endpoint)"""
    packages = db.query(VIPPackage).filter(VIPPackage.is_active == True).all()
    
    return [{
        "package_id": pkg.package_id,
        "name": pkg.name,
        "duration_months": pkg.duration_months,
        "price": pkg.price,
        "description": pkg.description,
        "package_type": pkg.package_type,
        "skill_type": pkg.skill_type
    } for pkg in packages]
@router.get("/subscription/status", response_model=dict)
async def get_subscription_status(
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Get current user's VIP subscription status - supports multiple skill-specific subscriptions"""
    # Get ALL active subscriptions (not just one)
    active_subscriptions = db.query(VIPSubscription).filter(
        VIPSubscription.user_id == current_user.user_id,
        VIPSubscription.end_date >= get_vietnam_time().replace(tzinfo=None),
        VIPSubscription.payment_status == "completed"
    ).order_by(VIPSubscription.end_date.desc()).all()
    
    if active_subscriptions:
        # Build skill access map
        skill_access = {
            "reading": False,
            "writing": False,
            "listening": False,
            "all_skills": False
        }
        
        subscriptions_list = []
        for sub in active_subscriptions:
            package = sub.package
            days_remaining = (sub.end_date - get_vietnam_time().replace(tzinfo=None)).days
            
            # Update skill access based on package type
            if package.package_type == "all_skills":
                skill_access["all_skills"] = True
                skill_access["reading"] = True
                skill_access["writing"] = True
                skill_access["listening"] = True
            elif package.package_type == "single_skill" and package.skill_type:
                skill_access[package.skill_type] = True
            
            subscriptions_list.append({
                "subscription_id": sub.subscription_id,
                "package_name": package.name,
                "package_type": package.package_type,
                "skill_type": package.skill_type,
                "start_date": sub.start_date,
                "end_date": sub.end_date,
                "days_remaining": days_remaining
            })
        
        # Get the subscription with the latest end_date for backward compatibility
        primary_subscription = active_subscriptions[0]
        
        return {
            "is_subscribed": True,
            # Backward compatibility fields (from primary subscription)
            "subscription_id": primary_subscription.subscription_id,
            "package_name": primary_subscription.package.name,
            "package_type": primary_subscription.package.package_type,
            "skill_type": primary_subscription.package.skill_type,
            "start_date": primary_subscription.start_date,
            "end_date": primary_subscription.end_date,
            "days_remaining": (primary_subscription.end_date - get_vietnam_time().replace(tzinfo=None)).days,
            # New fields for multiple subscriptions
            "subscriptions": subscriptions_list,
            "skill_access": skill_access,
            "has_reading_access": skill_access["reading"],
            "has_writing_access": skill_access["writing"],
            "has_listening_access": skill_access["listening"],
            "has_all_skills_access": skill_access["all_skills"]
        }
    
    return {
        "is_subscribed": False,
        "message": "No active VIP subscription",
        "subscriptions": [],
        "skill_access": {
            "reading": False,
            "writing": False,
            "listening": False,
            "all_skills": False
        },
        "has_reading_access": False,
        "has_writing_access": False,
        "has_listening_access": False,
        "has_all_skills_access": False
    }


@router.post("/packages/{package_id}/purchase", response_model=dict)
async def purchase_package(
    package_id: int,
    request: Request,
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Create a PayPal order for a VIP package purchase."""
    import os
    from app.utils.paypal_service import create_order

    package = db.query(VIPPackage).filter(
        VIPPackage.package_id == package_id,
        VIPPackage.is_active == True
    ).first()
    
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Package not found or not available"
        )
    
    # Rate limiting: max 20 payment requests per user per 5 minutes
    five_min_ago = get_vietnam_time().replace(tzinfo=None) - timedelta(minutes=5)
    recent_count = db.query(PackageTransaction).filter(
        PackageTransaction.user_id == current_user.user_id,
        PackageTransaction.created_at >= five_min_ago,
        PackageTransaction.status != "reject"
    ).count()
    
    if recent_count >= 20:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many payment requests. Please try again later."
        )
    
    # Cancel existing pending PayPal transactions for same user+package
    existing_pending = db.query(PackageTransaction).filter(
        PackageTransaction.user_id == current_user.user_id,
        PackageTransaction.package_id == package_id,
        PackageTransaction.status == "pending",
        PackageTransaction.payment_method == "paypal",
    ).all()
    
    for old_txn in existing_pending:
        old_txn.status = "reject"
        old_txn.admin_note = "Auto-cancelled: new transaction created"
        if old_txn.subscription_id:
            old_sub = db.query(VIPSubscription).filter(
                VIPSubscription.subscription_id == old_txn.subscription_id
            ).first()
            if old_sub and old_sub.payment_status == "pending":
                old_sub.payment_status = "reject"
    if existing_pending:
        db.commit()
    
    # Subscription stacking logic (unchanged)
    if package.package_type == "single_skill":
        active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
            VIPSubscription.user_id == current_user.user_id,
            VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
            VIPSubscription.payment_status == "completed",
            ((VIPPackage.package_type == "single_skill") & (VIPPackage.skill_type == package.skill_type)) |
            (VIPPackage.package_type == "all_skills")
        ).order_by(VIPSubscription.end_date.desc()).first()
    else:
        active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
            VIPSubscription.user_id == current_user.user_id,
            VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
            VIPSubscription.payment_status == "completed",
            VIPPackage.package_type == "all_skills"
        ).order_by(VIPSubscription.end_date.desc()).first()
    
    if active_subscription:
        start_date = active_subscription.end_date
    else:
        start_date = get_vietnam_time().replace(tzinfo=None)
    
    end_date = start_date + timedelta(days=package.duration_months * 30)
    
    # Create subscription (pending)
    subscription = VIPSubscription(
        user_id=current_user.user_id,
        package_id=package_id,
        start_date=start_date,
        end_date=end_date,
        payment_status="pending",
        created_at=get_vietnam_time().replace(tzinfo=None)
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    
    # Create transaction record
    transaction = PackageTransaction(
        user_id=current_user.user_id,
        package_id=package_id,
        subscription_id=subscription.subscription_id,
        amount=package.price,
        payment_method="paypal",
        status="pending",
        created_at=get_vietnam_time().replace(tzinfo=None)
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    
    # Create PayPal order
    try:
        return_url = os.getenv("PAYPAL_RETURN_URL", "")
        cancel_url = os.getenv("PAYPAL_CANCEL_URL", "")
        
        paypal_order = create_order(
            amount_usd=float(package.price),
            description=f"VIP Package: {package.name}",
            return_url=return_url,
            cancel_url=cancel_url,
            custom_id=str(transaction.transaction_id),
        )
        
        # Save PayPal order ID
        transaction.paypal_order_id = paypal_order["id"]
        db.commit()
        
        return {
            "message": "PayPal order created successfully",
            "transaction_id": transaction.transaction_id,
            "paypal_order_id": paypal_order["id"],
            "approve_url": paypal_order.get("approve_url"),
            "status": "pending"
        }
    except Exception as e:
        # If PayPal fails, clean up
        db.delete(transaction)
        db.delete(subscription)
        db.commit()
        logger.error(f"PayPal order creation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment. Please try again later."
        )


@router.post("/packages/{package_id}/capture", response_model=dict)
async def capture_payment(
    package_id: int,
    request: Request,
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """
    Capture a PayPal payment after user approval.
    Called by frontend after PayPal onApprove callback.
    """
    from app.utils.paypal_service import capture_order
    
    body = await request.json()
    paypal_order_id = body.get("paypal_order_id")
    
    if not paypal_order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing paypal_order_id"
        )
    
    # Find the transaction
    transaction = db.query(PackageTransaction).filter(
        PackageTransaction.paypal_order_id == paypal_order_id,
        PackageTransaction.user_id == current_user.user_id,
    ).first()
    
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found"
        )
    
    # Idempotency: if already completed, return success
    if transaction.status == "completed":
        return {
            "message": "Payment already completed",
            "transaction_id": transaction.transaction_id,
            "status": "completed"
        }
    
    # Capture the payment on PayPal
    try:
        capture_result = capture_order(paypal_order_id)
        
        if capture_result["status"] == "COMPLETED":
            # Payment successful — activate VIP
            transaction.status = "completed"
            transaction.admin_note = f"PayPal capture: {capture_result.get('capture_id', 'N/A')}"
            
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
            
            return {
                "message": "Payment completed successfully! VIP activated.",
                "transaction_id": transaction.transaction_id,
                "status": "completed"
            }
        else:
            # Payment not completed
            return {
                "message": f"Payment status: {capture_result['status']}",
                "transaction_id": transaction.transaction_id,
                "status": capture_result["status"].lower()
            }
    except Exception as e:
        logger.error(f"PayPal capture failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment capture failed. Please contact support."
        )


@router.post("/packages/{package_id}/verify-and-activate", response_model=dict)
async def verify_and_activate(
    package_id: int,
    request: Request,
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """
    Verify a client-side PayPal payment and activate VIP.
    Called AFTER PayPal JS SDK creates + captures an order on the client side.
    Backend verifies the order via PayPal API before activating VIP.
    """
    from app.utils.paypal_service import get_access_token, _get_base_url
    import httpx
    
    body = await request.json()
    paypal_order_id = body.get("paypal_order_id")
    
    if not paypal_order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing paypal_order_id"
        )
    
    # Get the package
    package = db.query(VIPPackage).filter(
        VIPPackage.package_id == package_id,
        VIPPackage.is_active == True
    ).first()
    
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Package not found or not available"
        )
    
    # Prevent duplicate activation
    existing = db.query(PackageTransaction).filter(
        PackageTransaction.paypal_order_id == paypal_order_id,
        PackageTransaction.status == "completed"
    ).first()
    
    if existing:
        return {
            "message": "Payment already processed",
            "transaction_id": existing.transaction_id,
            "status": "completed"
        }
    
    # Rate limiting: max 20 payment requests per user per 5 minutes
    five_min_ago = get_vietnam_time().replace(tzinfo=None) - timedelta(minutes=5)
    recent_count = db.query(PackageTransaction).filter(
        PackageTransaction.user_id == current_user.user_id,
        PackageTransaction.created_at >= five_min_ago,
        PackageTransaction.status != "reject"
    ).count()
    
    if recent_count >= 20:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many payment requests. Please try again later."
        )
    
    # Verify the order with PayPal API
    try:
        token = get_access_token()
        base_url = _get_base_url()
        
        with httpx.Client() as client:
            response = client.get(
                f"{base_url}/v2/checkout/orders/{paypal_order_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            order_details = response.json()
        
        order_status = order_details.get("status")
        
        # Verify payment is completed
        if order_status != "COMPLETED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payment not completed. Status: {order_status}"
            )
        
        # Verify the amount matches
        purchase_units = order_details.get("purchase_units", [])
        if not purchase_units:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid payment: no purchase units"
            )
        
        paid_amount = float(purchase_units[0].get("amount", {}).get("value", "0"))
        expected_amount = float(package.price)
        
        if abs(paid_amount - expected_amount) > 0.01:
            logger.warning(
                f"Amount mismatch for order {paypal_order_id}: "
                f"paid={paid_amount}, expected={expected_amount}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment amount does not match package price"
            )
        
        # Extract capture ID
        capture_id = None
        captures = purchase_units[0].get("payments", {}).get("captures", [])
        if captures:
            capture_id = captures[0].get("id")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PayPal order verification failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify payment. Please contact support."
        )
    
    # Payment verified — create records and activate VIP
    try:
        # Subscription stacking logic
        if package.package_type == "single_skill":
            active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
                VIPSubscription.user_id == current_user.user_id,
                VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
                VIPSubscription.payment_status == "completed",
                ((VIPPackage.package_type == "single_skill") & (VIPPackage.skill_type == package.skill_type)) |
                (VIPPackage.package_type == "all_skills")
            ).order_by(VIPSubscription.end_date.desc()).first()
        else:
            active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
                VIPSubscription.user_id == current_user.user_id,
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
            user_id=current_user.user_id,
            package_id=package_id,
            start_date=start_date,
            end_date=end_date,
            payment_status="completed",
            created_at=get_vietnam_time().replace(tzinfo=None)
        )
        db.add(subscription)
        db.commit()
        db.refresh(subscription)
        
        # Create transaction record (completed immediately)
        transaction = PackageTransaction(
            user_id=current_user.user_id,
            package_id=package_id,
            subscription_id=subscription.subscription_id,
            amount=package.price,
            payment_method="paypal",
            paypal_order_id=paypal_order_id,
            status="completed",
            admin_note=f"PayPal capture: {capture_id or 'N/A'}",
            created_at=get_vietnam_time().replace(tzinfo=None)
        )
        db.add(transaction)
        
        # Activate VIP
        current_user.is_vip = True
        if current_user.vip_expiry is None or end_date > current_user.vip_expiry:
            current_user.vip_expiry = end_date
        
        db.commit()
        
        logger.info(
            f"VIP activated for user {current_user.user_id}: "
            f"package={package.name}, order={paypal_order_id}, capture={capture_id}"
        )
        
        return {
            "message": "Payment verified and VIP activated!",
            "transaction_id": transaction.transaction_id,
            "status": "completed"
        }
    except Exception as e:
        db.rollback()
        logger.error(f"VIP activation failed after payment: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment was successful but VIP activation failed. Please contact support with your PayPal order ID."
        )


@router.post("/packages/{package_id}/server-capture", response_model=dict)
async def server_capture_and_activate(
    package_id: int,
    request: Request,
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """
    BULLETPROOF payment endpoint: Capture + Activate in ONE atomic step.
    
    Flow:
    1. Client creates order on PayPal (client-side) — no money moves
    2. User approves on PayPal popup — still no money moves
    3. Client sends order_id here — backend captures via PayPal API
    4. If capture succeeds → activate VIP immediately (same DB transaction)
    5. If capture fails → no money deducted, no VIP activated
    
    This guarantees: money is NEVER taken without VIP being activated.
    """
    from app.utils.paypal_service import capture_order, get_access_token, _get_base_url
    import httpx
    
    body = await request.json()
    paypal_order_id = body.get("paypal_order_id")
    
    if not paypal_order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing paypal_order_id"
        )
    
    # --- IDEMPOTENCY CHECK: if this order was already processed, return success ---
    existing = db.query(PackageTransaction).filter(
        PackageTransaction.paypal_order_id == paypal_order_id,
        PackageTransaction.status == "completed"
    ).first()
    
    if existing:
        logger.info(f"Idempotent hit: order {paypal_order_id} already completed")
        return {
            "message": "Payment already processed",
            "transaction_id": existing.transaction_id,
            "status": "completed"
        }
    
    # --- VALIDATE PACKAGE ---
    package = db.query(VIPPackage).filter(
        VIPPackage.package_id == package_id,
        VIPPackage.is_active == True
    ).first()
    
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Package not found or not available"
        )
    
    # --- RATE LIMIT ---
    five_min_ago = get_vietnam_time().replace(tzinfo=None) - timedelta(minutes=5)
    recent_count = db.query(PackageTransaction).filter(
        PackageTransaction.user_id == current_user.user_id,
        PackageTransaction.created_at >= five_min_ago,
        PackageTransaction.status != "reject"
    ).count()
    
    if recent_count >= 20:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many payment requests. Please try again later."
        )
    
    # --- STEP 1: Check order status first (maybe already captured by client-side) ---
    try:
        token = get_access_token()
        base_url = _get_base_url()
        
        with httpx.Client(timeout=30.0) as client:
            check_resp = client.get(
                f"{base_url}/v2/checkout/orders/{paypal_order_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            check_resp.raise_for_status()
            order_info = check_resp.json()
        
        order_status = order_info.get("status")
        logger.info(f"Order {paypal_order_id} current status: {order_status}")
        
    except Exception as e:
        logger.error(f"Failed to check order status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot reach PayPal to verify order. Please try again."
        )
    
    # --- STEP 2: Capture if not already captured ---
    capture_id = None
    paid_amount = 0.0
    
    if order_status == "APPROVED":
        # Order approved by user but NOT yet captured — capture it now
        try:
            capture_result = capture_order(paypal_order_id)
            
            if capture_result["status"] != "COMPLETED":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Payment capture failed. Status: {capture_result['status']}"
                )
            
            capture_id = capture_result.get("capture_id")
            
            # Extract paid amount from capture result
            raw = capture_result.get("raw", {})
            purchase_units = raw.get("purchase_units", [])
            if purchase_units:
                captures = purchase_units[0].get("payments", {}).get("captures", [])
                if captures:
                    paid_amount = float(captures[0].get("amount", {}).get("value", "0"))
            
            logger.info(f"Server captured order {paypal_order_id}: capture_id={capture_id}, amount=${paid_amount}")
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Server capture failed for {paypal_order_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Payment capture failed on PayPal side. Your account was NOT charged. Please try again."
            )
    
    elif order_status == "COMPLETED":
        # Already captured (maybe by a previous attempt or client-side fallback)
        purchase_units = order_info.get("purchase_units", [])
        if purchase_units:
            captures = purchase_units[0].get("payments", {}).get("captures", [])
            if captures:
                capture_id = captures[0].get("id")
                paid_amount = float(captures[0].get("amount", {}).get("value", "0"))
        
        logger.info(f"Order {paypal_order_id} was already captured: capture_id={capture_id}")
    
    else:
        # Order is in an unexpected state (CREATED, VOIDED, etc.)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Order cannot be processed. Current status: {order_status}. Please create a new payment."
        )
    
    # --- STEP 3: Verify amount ---
    expected_amount = float(package.price)
    if abs(paid_amount - expected_amount) > 0.01:
        logger.warning(
            f"Amount mismatch for order {paypal_order_id}: "
            f"paid={paid_amount}, expected={expected_amount}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount does not match package price"
        )
    
    # --- STEP 4: Activate VIP (same DB transaction as recording the payment) ---
    try:
        # Subscription stacking logic
        if package.package_type == "single_skill":
            active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
                VIPSubscription.user_id == current_user.user_id,
                VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
                VIPSubscription.payment_status == "completed",
                ((VIPPackage.package_type == "single_skill") & (VIPPackage.skill_type == package.skill_type)) |
                (VIPPackage.package_type == "all_skills")
            ).order_by(VIPSubscription.end_date.desc()).first()
        else:
            active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
                VIPSubscription.user_id == current_user.user_id,
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
            user_id=current_user.user_id,
            package_id=package_id,
            start_date=start_date,
            end_date=end_date,
            payment_status="completed",
            created_at=get_vietnam_time().replace(tzinfo=None)
        )
        db.add(subscription)
        db.flush()  # Get subscription_id without committing yet
        
        # Create transaction record (completed immediately)
        transaction = PackageTransaction(
            user_id=current_user.user_id,
            package_id=package_id,
            subscription_id=subscription.subscription_id,
            amount=package.price,
            payment_method="paypal",
            paypal_order_id=paypal_order_id,
            status="completed",
            admin_note=f"Server capture: {capture_id or 'N/A'}",
            created_at=get_vietnam_time().replace(tzinfo=None)
        )
        db.add(transaction)
        
        # Activate VIP on user
        current_user.is_vip = True
        if current_user.vip_expiry is None or end_date > current_user.vip_expiry:
            current_user.vip_expiry = end_date
        
        # SINGLE COMMIT: payment record + subscription + VIP activation
        db.commit()
        
        logger.info(
            f"VIP activated for user {current_user.user_id}: "
            f"package={package.name}, order={paypal_order_id}, capture={capture_id}"
        )
        
        return {
            "message": "Payment verified and VIP activated!",
            "transaction_id": transaction.transaction_id,
            "status": "completed"
        }
    except Exception as e:
        db.rollback()
        logger.error(f"VIP activation failed after capture: {e}", exc_info=True)
        # CRITICAL: Money was captured but VIP failed. Log prominently for manual recovery.
        logger.critical(
            f"PAYMENT CAPTURED BUT VIP FAILED! "
            f"user={current_user.user_id}, order={paypal_order_id}, capture={capture_id}, "
            f"package={package.name}, amount=${paid_amount}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment was captured but VIP activation failed. Please contact support with your PayPal order ID."
        )


@router.post("/packages/{package_id}/form-capture")
async def form_capture_and_redirect(
    package_id: int,
    token: str = Form(...),
    paypal_order_id: str = Form(...),
    return_origin: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    CORS-FREE payment capture via form POST.
    
    HTML form submissions with Content-Type: application/x-www-form-urlencoded
    do NOT trigger CORS preflight. The browser sends the POST directly.
    
    Flow:
    1. Frontend creates a hidden form with token, order_id, return_origin
    2. Form submits to this endpoint (cross-origin, but no CORS needed for forms)
    3. Backend captures payment + activates VIP
    4. Backend redirects user back to frontend success/failure page
    """
    from fastapi.responses import RedirectResponse
    from app.utils.paypal_service import capture_order, get_access_token, _get_base_url
    from jose import JWTError, jwt as jose_jwt
    import httpx
    
    success_url = f"{return_origin}/payment-success?from=form-capture"
    error_base = f"{return_origin}/payment-failure"
    
    # --- AUTHENTICATE via form token (same JWT, just not from header) ---
    try:
        secret_key = os.getenv("SECRET_KEY", "latest-secret-key-here-30-Oct")
        payload = jose_jwt.decode(token, secret_key, algorithms=["HS256"])
        username = payload.get("sub")
        if not username:
            return RedirectResponse(url=f"{error_base}?error=invalid_token", status_code=303)
        
        current_user = db.query(User).filter(User.username == username).first()
        if not current_user:
            return RedirectResponse(url=f"{error_base}?error=user_not_found", status_code=303)
    except JWTError:
        return RedirectResponse(url=f"{error_base}?error=expired_token", status_code=303)
    
    logger.info(f"[form-capture] User {current_user.user_id} capturing order {paypal_order_id}")
    
    # --- IDEMPOTENCY CHECK ---
    existing = db.query(PackageTransaction).filter(
        PackageTransaction.paypal_order_id == paypal_order_id,
        PackageTransaction.status == "completed"
    ).first()
    
    if existing:
        logger.info(f"[form-capture] Order {paypal_order_id} already completed")
        return RedirectResponse(url=success_url, status_code=303)
    
    # --- VALIDATE PACKAGE ---
    package = db.query(VIPPackage).filter(
        VIPPackage.package_id == package_id,
        VIPPackage.is_active == True
    ).first()
    
    if not package:
        return RedirectResponse(url=f"{error_base}?error=package_not_found", status_code=303)
    
    # --- CHECK ORDER STATUS + CAPTURE ---
    try:
        pp_token = get_access_token()
        base_url = _get_base_url()
        
        with httpx.Client(timeout=30.0) as client:
            check_resp = client.get(
                f"{base_url}/v2/checkout/orders/{paypal_order_id}",
                headers={
                    "Authorization": f"Bearer {pp_token}",
                    "Content-Type": "application/json",
                },
            )
            check_resp.raise_for_status()
            order_info = check_resp.json()
        
        order_status = order_info.get("status")
        logger.info(f"[form-capture] Order {paypal_order_id} status: {order_status}")
        
    except Exception as e:
        logger.error(f"[form-capture] PayPal check failed: {e}", exc_info=True)
        return RedirectResponse(
            url=f"{error_base}?error=paypal_unreachable&order_id={paypal_order_id}",
            status_code=303
        )
    
    capture_id = None
    paid_amount = 0.0
    
    if order_status == "APPROVED":
        try:
            capture_result = capture_order(paypal_order_id)
            if capture_result["status"] != "COMPLETED":
                return RedirectResponse(
                    url=f"{error_base}?error=capture_failed&status={capture_result['status']}&order_id={paypal_order_id}",
                    status_code=303
                )
            capture_id = capture_result.get("capture_id")
            raw = capture_result.get("raw", {})
            purchase_units = raw.get("purchase_units", [])
            if purchase_units:
                captures = purchase_units[0].get("payments", {}).get("captures", [])
                if captures:
                    paid_amount = float(captures[0].get("amount", {}).get("value", "0"))
        except Exception as e:
            logger.error(f"[form-capture] Capture failed: {e}", exc_info=True)
            return RedirectResponse(
                url=f"{error_base}?error=capture_exception&order_id={paypal_order_id}",
                status_code=303
            )
    elif order_status == "COMPLETED":
        purchase_units = order_info.get("purchase_units", [])
        if purchase_units:
            captures = purchase_units[0].get("payments", {}).get("captures", [])
            if captures:
                capture_id = captures[0].get("id")
                paid_amount = float(captures[0].get("amount", {}).get("value", "0"))
    else:
        return RedirectResponse(
            url=f"{error_base}?error=invalid_status&status={order_status}&order_id={paypal_order_id}",
            status_code=303
        )
    
    # --- VERIFY AMOUNT ---
    expected_amount = float(package.price)
    if abs(paid_amount - expected_amount) > 0.01:
        logger.warning(f"[form-capture] Amount mismatch: paid={paid_amount}, expected={expected_amount}")
        return RedirectResponse(
            url=f"{error_base}?error=amount_mismatch&order_id={paypal_order_id}",
            status_code=303
        )
    
    # --- ACTIVATE VIP ---
    try:
        if package.package_type == "single_skill":
            active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
                VIPSubscription.user_id == current_user.user_id,
                VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
                VIPSubscription.payment_status == "completed",
                ((VIPPackage.package_type == "single_skill") & (VIPPackage.skill_type == package.skill_type)) |
                (VIPPackage.package_type == "all_skills")
            ).order_by(VIPSubscription.end_date.desc()).first()
        else:
            active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
                VIPSubscription.user_id == current_user.user_id,
                VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
                VIPSubscription.payment_status == "completed",
                VIPPackage.package_type == "all_skills"
            ).order_by(VIPSubscription.end_date.desc()).first()
        
        start_date = active_subscription.end_date if active_subscription else get_vietnam_time().replace(tzinfo=None)
        end_date = start_date + timedelta(days=package.duration_months * 30)
        
        subscription = VIPSubscription(
            user_id=current_user.user_id,
            package_id=package_id,
            start_date=start_date,
            end_date=end_date,
            payment_status="completed",
            created_at=get_vietnam_time().replace(tzinfo=None)
        )
        db.add(subscription)
        db.flush()
        
        transaction = PackageTransaction(
            user_id=current_user.user_id,
            package_id=package_id,
            subscription_id=subscription.subscription_id,
            amount=package.price,
            payment_method="paypal",
            paypal_order_id=paypal_order_id,
            status="completed",
            admin_note=f"Form capture: {capture_id or 'N/A'}",
            created_at=get_vietnam_time().replace(tzinfo=None)
        )
        db.add(transaction)
        
        current_user.is_vip = True
        if current_user.vip_expiry is None or end_date > current_user.vip_expiry:
            current_user.vip_expiry = end_date
        
        db.commit()
        
        logger.info(
            f"[form-capture] VIP activated for user {current_user.user_id}: "
            f"package={package.name}, order={paypal_order_id}, capture={capture_id}"
        )
        
        return RedirectResponse(url=success_url, status_code=303)
        
    except Exception as e:
        db.rollback()
        logger.error(f"[form-capture] VIP activation failed: {e}", exc_info=True)
        logger.critical(
            f"[form-capture] PAYMENT CAPTURED BUT VIP FAILED! "
            f"user={current_user.user_id}, order={paypal_order_id}, capture={capture_id}"
        )
        return RedirectResponse(
            url=f"{error_base}?error=activation_failed&order_id={paypal_order_id}",
            status_code=303
        )

@router.get("/subscription/history", response_model=List[dict])
async def get_subscription_history(
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Get user's VIP subscription history"""
    subscriptions = db.query(VIPSubscription).filter(
        VIPSubscription.user_id == current_user.user_id
    ).order_by(VIPSubscription.created_at.desc()).all()
    
    return [{
        "subscription_id": sub.subscription_id,
        "package_name": sub.package.name,
        "start_date": sub.start_date,
        "end_date": sub.end_date,
        "payment_status": sub.payment_status,
        "is_active": sub.end_date > get_vietnam_time().replace(tzinfo=None) and sub.payment_status == "completed"
    } for sub in subscriptions]


@router.get("/transactions/{transaction_id}/status", response_model=dict)
async def get_transaction_status(
    transaction_id: int,
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Get detailed transaction status"""
    transaction = db.query(PackageTransaction).filter(
        PackageTransaction.transaction_id == transaction_id,
        PackageTransaction.user_id == current_user.user_id
    ).first()
    
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found"
        )
    
    waiting_time = (get_vietnam_time().replace(tzinfo=None) - transaction.created_at).total_seconds() // 60  # minutes
    
    return {
        "transaction_id": transaction.transaction_id,
        "status": transaction.status,
        "admin_note": transaction.admin_note,
        "created_at": transaction.created_at,
        "package_name": transaction.package.name,
        "amount": float(transaction.amount),
        "payment_method": transaction.payment_method,
        "bank_description": transaction.bank_description,
        "bank_transfer_image": transaction.bank_transfer_image,
        "waiting_time_minutes": waiting_time,
        "subscription_status": transaction.subscription.payment_status if transaction.subscription else None,
        "is_completed": transaction.status == "completed",
        "last_updated": transaction.created_at  # Changed from updated_at to created_at
    }

@router.get("/remaining-days", response_model=dict)
async def get_vip_remaining_days(
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Get the remaining days of the user's VIP subscription"""
    active_subscription = db.query(VIPSubscription).filter(
        VIPSubscription.user_id == current_user.user_id,
        VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
        VIPSubscription.payment_status == "completed"
    ).first()
    
    if not active_subscription:
        return {
            "has_active_subscription": False,
            "remaining_days": 0,
            "message": "No active VIP subscription found"
        }
    
    remaining_days = (active_subscription.end_date - get_vietnam_time().replace(tzinfo=None)).days
    
    return {
        "has_active_subscription": True,
        "remaining_days": remaining_days,
        "end_date": active_subscription.end_date,
        "package_name": active_subscription.package.name
    }
