from fastapi import APIRouter, Depends, HTTPException, status, Form
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import PackageTransaction, VIPSubscription, User, VIPPackage
from app.routes.admin.auth import get_current_admin
from datetime import datetime
from typing import List
from sqlalchemy.orm import joinedload
from sqlalchemy import func, case, select
from pydantic import BaseModel
from app.utils.datetime_utils import get_vietnam_time
from app.utils.currency import vnd_to_usd, USD_VND_RATE

router = APIRouter()

# Lemon Squeezy rows store the USD charged; PayOS rows store VND. The admin UI reads
# every transaction amount as VND, so LS amounts are converted at the site rate.
_AMOUNT_VND = case(
    (PackageTransaction.payment_method == "lemonsqueezy", PackageTransaction.amount * USD_VND_RATE),
    else_=PackageTransaction.amount,
)


def _amount_vnd(tx) -> float:
    amount = float(tx.amount or 0)
    return amount * USD_VND_RATE if tx.payment_method == "lemonsqueezy" else amount

# Skills sold as single-skill packages. Speaking is only ever sold this way: the
# 'all_skills' bundle covers Listening + Reading (app/utils/vip_access.py).
VALID_SKILL_TYPES = ('reading', 'writing', 'listening', 'speaking')


class TransactionUpdate(BaseModel):
    status: str
    admin_note: str | None = None

@router.post("/packages", response_model=dict)
async def create_vip_package(
    name: str,
    duration_months: int,
    price: float,
    package_type: str,
    skill_type: str = None,
    description: str = None,
    ls_variant_id: str = None,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    # Validate package type
    if package_type not in ['all_skills', 'single_skill']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Package type must be 'all_skills' or 'single_skill'"
        )
    
    # Validate skill type for single skill packages
    if package_type == 'single_skill':
        if skill_type not in VALID_SKILL_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Skill type must be 'reading', 'writing', 'listening' or 'speaking'"
            )
    
    package = VIPPackage(
        name=name,
        duration_months=duration_months,
        price=price,
        package_type=package_type,
        skill_type=skill_type,
        description=description,
        ls_variant_id=ls_variant_id,
        is_active=True,
        created_at=get_vietnam_time().replace(tzinfo=None)
    )
    db.add(package)
    db.commit()
    db.refresh(package)
    return {"message": "VIP package created successfully", "package_id": package.package_id}

@router.get("/packages", response_model=List[dict])
async def get_all_packages(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    packages = db.query(VIPPackage).all()
    return [
        {
            "package_id": p.package_id,
            "name": p.name,
            "duration_months": p.duration_months,
            "package_type": p.package_type,
            "price": p.price,
            "price_usd": vnd_to_usd(p.price),   # what customers see
            "description": p.description,
            "is_active": p.is_active,
            "ls_variant_id": p.ls_variant_id,
            "created_at": p.created_at
        } for p in packages
    ]

@router.put("/packages/{package_id}", response_model=dict)
async def update_package(
    package_id: int,
    name: str = None,
    price: float = None,
    description: str = None,
    is_active: bool = None,
    package_type: str = None,
    skill_type: str = None,
    duration_months: int = None,
    ls_variant_id: str = None,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    package = db.query(VIPPackage).filter(VIPPackage.package_id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    
    # Validate package type if provided
    if package_type is not None:
        if package_type not in ['all_skills', 'single_skill']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Package type must be 'all_skills' or 'single_skill'"
            )
        package.package_type = package_type
        
        # Validate skill type for single skill packages
        if package_type == 'single_skill':
            if skill_type not in VALID_SKILL_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Skill type must be 'reading', 'writing', 'listening' or 'speaking'"
                )
            package.skill_type = skill_type
        else:
            package.skill_type = None
    
    if name is not None:
        package.name = name
    if price is not None:
        package.price = price
    if description is not None:
        package.description = description
    if is_active is not None:
        package.is_active = is_active
    if duration_months is not None:
        package.duration_months = duration_months
    if ls_variant_id is not None:
        package.ls_variant_id = ls_variant_id
    
    db.commit()
    return {"message": "Package updated successfully"}

@router.delete("/packages/{package_id}", response_model=dict)
async def delete_package(
    package_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Delete a VIP package"""
    package = db.query(VIPPackage).filter(VIPPackage.package_id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    
    # Check for active subscriptions
    active_subs = db.query(VIPSubscription).filter(
        VIPSubscription.package_id == package_id,
        VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
        VIPSubscription.payment_status == "completed"
    ).count()
    
    if active_subs > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Không thể xóa gói đang có {active_subs} đăng ký hoạt động"
        )
    
    # Delete related transactions and subscriptions first
    db.query(PackageTransaction).filter(PackageTransaction.package_id == package_id).delete()
    db.query(VIPSubscription).filter(VIPSubscription.package_id == package_id).delete()
    db.delete(package)
    db.commit()
    
    return {"message": "Package deleted successfully"}

@router.get("/subscriptions", response_model=List[dict])
async def get_all_subscriptions(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    # Eager load related user and package data to avoid N+1 queries
    subscriptions = db.query(VIPSubscription).options(
        joinedload(VIPSubscription.user),
        joinedload(VIPSubscription.package)
    ).all()
    
    result = []
    for s in subscriptions:
        package_info = {
            "package_id": s.package.package_id,
            "name": s.package.name,
            "duration_months": s.package.duration_months,
            "price": s.package.price,
            "package_type": s.package.package_type,
            "skill_type": s.package.skill_type,
            "description": s.package.description
        }
        
        # Get associated transaction for this subscription
        transaction = db.query(PackageTransaction).filter(
            PackageTransaction.subscription_id == s.subscription_id
        ).first()
        
        transaction_info = None
        if transaction:
            transaction_info = {
                "transaction_id": transaction.transaction_id,
                "amount": _amount_vnd(transaction),
                "payment_method": transaction.payment_method,
                "status": transaction.status,
                "admin_note": transaction.admin_note,
                "ls_order_id": transaction.ls_order_id,
                "created_at": transaction.created_at
            }
        
        result.append({
            "subscription_id": s.subscription_id,
            "user_id": s.user_id,
            "user_email": s.user.email,
            "username": s.user.username,
            "package": package_info,
            "start_date": s.start_date,
            "end_date": s.end_date,
            "payment_status": s.payment_status,
            "created_at": s.created_at,
            "transaction": transaction_info
        })
        
    return result
@router.get("/transactions/pending", response_model=List[dict])
async def get_pending_transactions(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all pending transactions"""
    transactions = db.query(PackageTransaction).filter(
        PackageTransaction.status == "pending"
    ).order_by(PackageTransaction.created_at.desc()).all()
    
    return [{
        "transaction_id": tx.transaction_id,
        "user_email": tx.user.email,
        "package_name": tx.package.name,
        "amount": _amount_vnd(tx),
        "payment_method": tx.payment_method,
        "bank_description": tx.bank_description,
        "transaction_code": tx.transaction_code,
        "bank_transfer_image": tx.bank_transfer_image,
        "created_at": tx.created_at
    } for tx in transactions]

@router.put("/transactions/{transaction_id}", response_model=dict)
async def update_transaction_status(
    transaction_id: int,
    transaction_status: str = Form(...),  # Changed from status to transaction_status
    admin_note: str = Form(None),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update transaction status"""
    # Validate status value
    if transaction_status not in ["pending", "completed", "reject"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid status value. Must be 'pending', 'completed', or 'reject'"
        )
    
    transaction = db.query(PackageTransaction).filter(
        PackageTransaction.transaction_id == transaction_id
    ).first()
    
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found"
        )
    
    transaction.status = transaction_status
    transaction.admin_note = admin_note
    
    if transaction_status == "completed":
        # Update subscription status
        subscription = db.query(VIPSubscription).filter(
            VIPSubscription.subscription_id == transaction.subscription_id
        ).first()
        
        if subscription:
            subscription.payment_status = "completed"
            
            # Update user VIP status
            user = db.query(User).filter(User.user_id == transaction.user_id).first()
            if user:
                user.is_vip = True
                user.vip_expiry = subscription.end_date
    elif transaction_status == "reject":
        # Update subscription status when rejected
        subscription = db.query(VIPSubscription).filter(
            VIPSubscription.subscription_id == transaction.subscription_id
        ).first()
        
        if subscription:
            subscription.payment_status = "reject"
    
    db.commit()
    
    return {
        "message": "Transaction status updated successfully",
        "transaction_id": transaction_id,
        "new_status": transaction_status
    }


@router.get("/dashboard/packages", response_model=dict)
async def get_packages_dashboard(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get package statistics for dashboard"""
    # Get total packages
    total_packages = db.query(VIPPackage).count()
    active_packages = db.query(VIPPackage).filter(VIPPackage.is_active == True).count()
    
    # Get subscription statistics
    total_subscriptions = db.query(VIPSubscription).count()
    active_subscriptions = db.query(VIPSubscription).filter(
        VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
        VIPSubscription.payment_status == "completed"
    ).count()
    
    # Get package-wise subscription counts
    package_stats = db.query(
        VIPPackage.package_id,
        VIPPackage.name,
        VIPPackage.price,
        func.count(VIPSubscription.subscription_id).label('total_subscriptions'),
        func.sum(
            case(
                (VIPSubscription.payment_status == 'completed', 1),
                else_=0
            )
        ).label('paid_subscriptions')
    ).outerjoin(VIPSubscription).group_by(VIPPackage.package_id).all()
    
    # Get recent transactions
    recent_transactions = db.query(PackageTransaction).order_by(
        PackageTransaction.created_at.desc()
    ).limit(5).all()
    
    return {
        "summary": {
            "total_packages": total_packages,
            "active_packages": active_packages,
            "total_subscriptions": total_subscriptions,
            "active_subscriptions": active_subscriptions
        },
        "package_statistics": [{
            "package_id": stat.package_id,
            "name": stat.name,
            "price": float(stat.price),
            "total_subscriptions": stat.total_subscriptions,
            "paid_subscriptions": stat.paid_subscriptions or 0,
            "revenue": float(stat.paid_subscriptions or 0) * float(stat.price)
        } for stat in package_stats],
        "recent_transactions": [{
            "transaction_id": tx.transaction_id,
            "user_email": tx.user.email,
            "package_name": tx.package.name,
            "amount": _amount_vnd(tx),
            "status": tx.status,
            "created_at": tx.created_at
        } for tx in recent_transactions]
    }

# Add after existing endpoints

@router.get("/dashboard/revenue", response_model=dict)
async def get_total_revenue(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get total revenue from completed transactions"""
    from datetime import timedelta
    now = get_vietnam_time().replace(tzinfo=None)

    # Calculate total revenue
    total_revenue = db.query(func.sum(_AMOUNT_VND)).filter(
        PackageTransaction.status == "completed"
    ).scalar() or 0

    # Today's revenue
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_revenue = db.query(func.sum(_AMOUNT_VND)).filter(
        PackageTransaction.status == "completed",
        PackageTransaction.created_at >= today_start
    ).scalar() or 0

    # This month's revenue
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_revenue = db.query(func.sum(_AMOUNT_VND)).filter(
        PackageTransaction.status == "completed",
        PackageTransaction.created_at >= month_start
    ).scalar() or 0

    # This year's revenue
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    year_revenue = db.query(func.sum(_AMOUNT_VND)).filter(
        PackageTransaction.status == "completed",
        PackageTransaction.created_at >= year_start
    ).scalar() or 0

    # Weekly revenue for the last 12 weeks
    twelve_weeks_ago = now - timedelta(weeks=12)
    weekly_revenue_raw = db.query(
        func.date(PackageTransaction.created_at).label('date'),
        func.sum(_AMOUNT_VND).label('revenue')
    ).filter(
        PackageTransaction.status == "completed",
        PackageTransaction.created_at >= twelve_weeks_ago
    ).group_by(
        func.date(PackageTransaction.created_at)
    ).order_by(
        func.date(PackageTransaction.created_at)
    ).all()

    # Group daily data into weeks
    from collections import defaultdict
    weekly_buckets = defaultdict(float)
    for date_val, revenue in weekly_revenue_raw:
        from datetime import date as date_type
        if isinstance(date_val, str):
            d = datetime.strptime(date_val, "%Y-%m-%d").date()
        else:
            d = date_val
        # Get the Monday of that week
        week_start = d - timedelta(days=d.weekday())
        weekly_buckets[week_start] += float(revenue)

    weekly_revenue_list = [
        {"week_start": str(k), "revenue": v}
        for k, v in sorted(weekly_buckets.items())
    ]

    # Monthly revenue for current year
    current_year = now.year
    monthly_revenue = db.query(
        func.extract('month', PackageTransaction.created_at).label('month'),
        func.sum(_AMOUNT_VND).label('revenue')
    ).filter(
        PackageTransaction.status == "completed",
        func.extract('year', PackageTransaction.created_at) == current_year
    ).group_by(
        func.extract('month', PackageTransaction.created_at)
    ).order_by(
        func.extract('month', PackageTransaction.created_at)
    ).all()

    # Yearly revenue
    yearly_revenue = db.query(
        func.extract('year', PackageTransaction.created_at).label('year'),
        func.sum(_AMOUNT_VND).label('revenue')
    ).filter(
        PackageTransaction.status == "completed"
    ).group_by(
        func.extract('year', PackageTransaction.created_at)
    ).order_by(
        func.extract('year', PackageTransaction.created_at)
    ).all()
    
    return {
        "total_revenue": float(total_revenue),
        "today_revenue": float(today_revenue),
        "month_revenue": float(month_revenue),
        "year_revenue": float(year_revenue),
        "weekly_revenue": weekly_revenue_list,
        "monthly_revenue": [{
            "month": int(month),
            "revenue": float(revenue)
        } for month, revenue in monthly_revenue],
        "yearly_revenue": [{
            "year": int(year),
            "revenue": float(revenue)
        } for year, revenue in yearly_revenue]
    }


def _revenue_events():
    """Every payment actually taken in, as (amount, created_at) rows. The global site
    has one revenue source: completed VIP package transactions."""
    return select(
        _AMOUNT_VND.label("amount"),
        PackageTransaction.created_at.label("created_at"),
    ).where(PackageTransaction.status == "completed").subquery()


@router.get("/dashboard/revenue-detail", response_model=dict)
async def get_revenue_detail(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Detailed revenue analytics: daily, monthly, quarterly, yearly with comparisons
    (ported from the Vietnam tree). Amounts are VND, as collected."""
    rev = _revenue_events()
    from datetime import timedelta
    from calendar import monthrange
    now = get_vietnam_time().replace(tzinfo=None)

    # ── DAILY ──
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    today_rev = float(db.query(func.coalesce(func.sum(rev.c.amount), 0)).filter(
        rev.c.created_at >= today_start
    ).scalar())

    yesterday_rev = float(db.query(func.coalesce(func.sum(rev.c.amount), 0)).filter(
        rev.c.created_at >= yesterday_start,
        rev.c.created_at < today_start
    ).scalar())

    daily_change = round(((today_rev - yesterday_rev) / yesterday_rev) * 100, 1) if yesterday_rev > 0 else None

    # ── MONTHLY ──
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 1:
        prev_month_start = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        prev_month_start = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)

    current_month_rev = float(db.query(func.coalesce(func.sum(rev.c.amount), 0)).filter(
        rev.c.created_at >= month_start
    ).scalar())

    prev_month_rev = float(db.query(func.coalesce(func.sum(rev.c.amount), 0)).filter(
        rev.c.created_at >= prev_month_start,
        rev.c.created_at < month_start
    ).scalar())

    monthly_change = round(((current_month_rev - prev_month_rev) / prev_month_rev) * 100, 1) if prev_month_rev > 0 else None

    # ── QUARTERLY ──
    def get_quarter(month):
        return (month - 1) // 3 + 1

    def quarter_start_month(q):
        return (q - 1) * 3 + 1

    current_q = get_quarter(now.month)
    current_q_start = now.replace(month=quarter_start_month(current_q), day=1, hour=0, minute=0, second=0, microsecond=0)

    # Previous quarter
    if current_q == 1:
        prev_q = 4
        prev_q_year = now.year - 1
    else:
        prev_q = current_q - 1
        prev_q_year = now.year

    prev_q_start = datetime(prev_q_year, quarter_start_month(prev_q), 1, 0, 0, 0)
    prev_q_end = current_q_start

    current_q_rev = float(db.query(func.coalesce(func.sum(rev.c.amount), 0)).filter(
        rev.c.created_at >= current_q_start
    ).scalar())

    prev_q_rev = float(db.query(func.coalesce(func.sum(rev.c.amount), 0)).filter(
        rev.c.created_at >= prev_q_start,
        rev.c.created_at < prev_q_end
    ).scalar())

    quarterly_change = round(((current_q_rev - prev_q_rev) / prev_q_rev) * 100, 1) if prev_q_rev > 0 else None

    # Full quarterly breakdown across ALL years
    quarter_breakdown_raw = db.query(
        func.extract('year', rev.c.created_at).label('year'),
        func.extract('month', rev.c.created_at).label('month'),
        func.sum(rev.c.amount).label('revenue')
    ).group_by(
        func.extract('year', rev.c.created_at),
        func.extract('month', rev.c.created_at)
    ).all()

    # Group monthly data into quarters
    from collections import defaultdict
    quarter_buckets = defaultdict(float)
    for year_val, month_val, revenue in quarter_breakdown_raw:
        y = int(year_val)
        q = get_quarter(int(month_val))
        quarter_buckets[(y, q)] += float(revenue)

    quarter_breakdown = sorted([
        {
            "year": y,
            "quarter": f"Q{q}",
            "quarter_number": q,
            "months": f"{quarter_start_month(q)}-{quarter_start_month(q) + 2}",
            "revenue": rev
        }
        for (y, q), rev in quarter_buckets.items()
    ], key=lambda x: (x["year"], x["quarter_number"]))

    # ── YEARLY ──
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_year_start = datetime(now.year - 1, 1, 1, 0, 0, 0)

    current_year_rev = float(db.query(func.coalesce(func.sum(rev.c.amount), 0)).filter(
        rev.c.created_at >= year_start
    ).scalar())

    prev_year_rev = float(db.query(func.coalesce(func.sum(rev.c.amount), 0)).filter(
        rev.c.created_at >= prev_year_start,
        rev.c.created_at < year_start
    ).scalar())

    yearly_change = round(((current_year_rev - prev_year_rev) / prev_year_rev) * 100, 1) if prev_year_rev > 0 else None

    return {
        "daily": {
            "today": today_rev,
            "yesterday": yesterday_rev,
            "change_percent": daily_change
        },
        "monthly": {
            "current_month": current_month_rev,
            "current_month_label": f"{now.month}/{now.year}",
            "previous_month": prev_month_rev,
            "previous_month_label": f"{prev_month_start.month}/{prev_month_start.year}",
            "change_percent": monthly_change
        },
        "quarterly": {
            "current_quarter": current_q_rev,
            "current_quarter_label": f"Q{current_q}/{now.year}",
            "previous_quarter": prev_q_rev,
            "previous_quarter_label": f"Q{prev_q}/{prev_q_year}",
            "change_percent": quarterly_change,
            "quarter_breakdown": quarter_breakdown
        },
        "yearly": {
            "current_year": current_year_rev,
            "current_year_label": str(now.year),
            "previous_year": prev_year_rev,
            "previous_year_label": str(now.year - 1),
            "change_percent": yearly_change
        }
    }
