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
    """Create a PayOS payment link for a VIP package purchase."""
    import os
    import time
    from app.utils.payos_service import create_payment_link

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
        PackageTransaction.status != "reject"  # Don't count cancelled attempts
    ).count()
    
    if recent_count >= 20:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Bạn đã tạo quá nhiều yêu cầu thanh toán. Vui lòng thử lại sau."
        )
    
    # Cancel existing pending PayOS transactions for same user+package (they may have expired on PayOS side)
    existing_pending = db.query(PackageTransaction).filter(
        PackageTransaction.user_id == current_user.user_id,
        PackageTransaction.package_id == package_id,
        PackageTransaction.status == "pending",
        PackageTransaction.payment_method == "payos",
    ).all()
    
    for old_txn in existing_pending:
        old_txn.status = "reject"
        old_txn.admin_note = "Tự động hủy: tạo giao dịch mới"
        # Also cancel the linked subscription
        if old_txn.subscription_id:
            old_sub = db.query(VIPSubscription).filter(
                VIPSubscription.subscription_id == old_txn.subscription_id
            ).first()
            if old_sub and old_sub.payment_status == "pending":
                old_sub.payment_status = "reject"
    if existing_pending:
        db.commit()
    
    # Subscription stacking logic (unchanged from original)
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
    
    # Generate unique order code for PayOS (must be a unique positive integer)
    order_code = int(f"{int(time.time())}{subscription.subscription_id}")
    
    # Create transaction record
    transaction = PackageTransaction(
        user_id=current_user.user_id,
        package_id=package_id,
        subscription_id=subscription.subscription_id,
        amount=package.price,
        payment_method="payos",
        status="pending",
        payos_order_code=order_code,
        created_at=get_vietnam_time().replace(tzinfo=None)
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    
    # Create PayOS payment link
    try:
        return_url = os.getenv("PAYOS_RETURN_URL", "https://thiieltstrenmay.com/payment-success")
        cancel_url = os.getenv("PAYOS_CANCEL_URL", "https://thiieltstrenmay.com/payment-cancel")
        
        # PayOS description max 25 chars - strip "(XX ngày)" suffix
        clean_name = package.name.split("(")[0].strip()
        description = clean_name[:25]
        
        payos_response = create_payment_link(
            order_code=order_code,
            amount=int(package.price),
            description=description,
            return_url=return_url,
            cancel_url=cancel_url,
        )
        
        checkout_url = payos_response.checkout_url
        
        # Save checkout URL for reference
        transaction.payos_checkout_url = checkout_url
        db.commit()
        
        return {
            "message": "Payment link created successfully",
            "transaction_id": transaction.transaction_id,
            "checkoutUrl": checkout_url,
            "status": "pending"
        }
    except Exception as e:
        # If PayOS fails, clean up
        db.delete(transaction)
        db.delete(subscription)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Không thể tạo liên kết thanh toán. Vui lòng thử lại sau."
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
