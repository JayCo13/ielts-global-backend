from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import VIPPackage, VIPSubscription, User, PackageTransaction
from app.routes.admin.auth import get_current_student
from datetime import datetime, timedelta
from typing import List
from pydantic import BaseModel
import os
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
                "days_remaining": days_remaining,
                "is_auto_renew": sub.is_auto_renew,
                "ls_subscription_id": sub.ls_subscription_id,
                "cancelled_at": sub.cancelled_at,
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
            "is_auto_renew": primary_subscription.is_auto_renew,
            "ls_subscription_id": primary_subscription.ls_subscription_id,
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


# ========================
# Lemon Squeezy Endpoints
# ========================

@router.post("/packages/{package_id}/create-checkout", response_model=dict)
async def create_checkout(
    package_id: int,
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """
    Create a Lemon Squeezy checkout session for a VIP package subscription.
    Returns a checkout URL that the frontend opens as an overlay.
    """
    from app.utils.lemonsqueezy_service import create_checkout as ls_create_checkout

    package = db.query(VIPPackage).filter(
        VIPPackage.package_id == package_id,
        VIPPackage.is_active == True
    ).first()
    
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Package not found or not available"
        )
    
    if not package.ls_variant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This package is not configured for payment yet. Please contact support."
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
    
    # Create Lemon Squeezy checkout
    try:
        result = ls_create_checkout(
            variant_id=package.ls_variant_id,
            custom_data={
                "user_id": str(current_user.user_id),
                "package_id": str(package_id),
            },
            user_email=current_user.email or "",
            user_name=current_user.username or "",
        )
        
        return {
            "message": "Checkout created successfully",
            "checkout_url": result["checkout_url"],
            "checkout_id": result["checkout_id"],
            "package_name": package.name,
            "price": float(package.price),
        }
    except Exception as e:
        logger.error(f"Lemon Squeezy checkout creation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment checkout. Please try again later."
        )


@router.post("/subscription/cancel", response_model=dict)
async def cancel_subscription(
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """
    Cancel the user's active Lemon Squeezy subscription.
    VIP stays active until the end of the current billing period.
    """
    from app.utils.lemonsqueezy_service import cancel_subscription as ls_cancel

    # Find active subscription with LS subscription ID
    active_sub = db.query(VIPSubscription).filter(
        VIPSubscription.user_id == current_user.user_id,
        VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
        VIPSubscription.payment_status == "completed",
        VIPSubscription.ls_subscription_id.isnot(None),
        VIPSubscription.is_auto_renew == True,
    ).order_by(VIPSubscription.end_date.desc()).first()

    if not active_sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription found to cancel."
        )
    
    try:
        result = ls_cancel(active_sub.ls_subscription_id)
        
        # Update local records
        active_sub.is_auto_renew = False
        active_sub.cancelled_at = get_vietnam_time().replace(tzinfo=None)
        db.commit()
        
        return {
            "message": "Subscription cancelled. Your VIP access will remain active until the end of the current billing period.",
            "ends_at": result.get("ends_at"),
            "subscription_id": active_sub.subscription_id,
        }
    except Exception as e:
        logger.error(f"Failed to cancel LS subscription: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel subscription. Please try again or contact support."
        )


@router.post("/subscription/resume", response_model=dict)
async def resume_subscription(
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """
    Resume a cancelled Lemon Squeezy subscription (before it expires).
    Re-enables auto-renewal so the subscription continues.
    """
    from app.utils.lemonsqueezy_service import resume_subscription as ls_resume

    # Find cancelled subscription that hasn't expired yet
    cancelled_sub = db.query(VIPSubscription).filter(
        VIPSubscription.user_id == current_user.user_id,
        VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
        VIPSubscription.payment_status == "completed",
        VIPSubscription.ls_subscription_id.isnot(None),
        VIPSubscription.is_auto_renew == False,
        VIPSubscription.cancelled_at.isnot(None),
    ).order_by(VIPSubscription.end_date.desc()).first()

    if not cancelled_sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No cancelled subscription found to resume."
        )

    try:
        result = ls_resume(cancelled_sub.ls_subscription_id)

        # Update local records
        cancelled_sub.is_auto_renew = True
        cancelled_sub.cancelled_at = None
        db.commit()

        return {
            "message": "Subscription resumed! Auto-renewal is now active.",
            "renews_at": result.get("renews_at"),
            "subscription_id": cancelled_sub.subscription_id,
        }
    except Exception as e:
        logger.error(f"Failed to resume LS subscription: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resume subscription. Please try again or contact support."
        )


@router.get("/subscription/manage", response_model=dict)
async def get_manage_url(
    current_user: User = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """
    Get Lemon Squeezy Customer Portal URL for billing management.
    The URL is pre-signed and valid for 24 hours.
    """
    from app.utils.lemonsqueezy_service import get_subscription as ls_get_sub

    # Find active subscription with LS subscription ID
    active_sub = db.query(VIPSubscription).filter(
        VIPSubscription.user_id == current_user.user_id,
        VIPSubscription.ls_subscription_id.isnot(None),
    ).order_by(VIPSubscription.end_date.desc()).first()

    if not active_sub or not active_sub.ls_subscription_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found. Please subscribe to a VIP package first."
        )
    
    try:
        result = ls_get_sub(active_sub.ls_subscription_id)
        
        portal_url = result.get("customer_portal_url")
        if not portal_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Customer portal URL not available. Please try again later."
            )
        
        return {
            "customer_portal_url": portal_url,
            "update_payment_method_url": result.get("update_payment_method_url"),
            "status": result.get("status"),
            "renews_at": result.get("renews_at"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get LS manage URL: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get billing management link. Please try again later."
        )


# ========================
# Read-only Endpoints (kept from before)
# ========================

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
        "is_active": sub.end_date > get_vietnam_time().replace(tzinfo=None) and sub.payment_status == "completed",
        "is_auto_renew": sub.is_auto_renew,
        "cancelled_at": sub.cancelled_at,
        "ls_subscription_id": sub.ls_subscription_id,
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
        "package_name": active_subscription.package.name,
        "is_auto_renew": active_subscription.is_auto_renew,
    }
