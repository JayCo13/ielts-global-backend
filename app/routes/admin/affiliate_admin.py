"""Admin affiliate management (ported from the VN tree): overview totals, withdrawal
request list + detail, and mark-paid / reject actions. Payouts are manual — the admin
transfers the money, then marks the request paid.

Global differences: commissions are credited by reconciliation (`sync_commissions`),
run when the overview loads or via POST /admin/affiliate/sync. VN's manual
"attribute a referrer" tools are not ported: they exist to repair a historical VN
bug and their "no backfill" option conflicts with reconciliation.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import User, AffiliateWalletTx, AffiliateWithdrawal
from app.routes.admin.auth import get_current_admin
from app.utils.datetime_utils import get_vietnam_time
from app.utils.affiliate import sync_commissions, AFFILIATE_CURRENCY, UNIT_DIVISOR, WITHDRAW_MIN_XU

router = APIRouter()


def _now():
    return get_vietnam_time().replace(tzinfo=None)


def _days_waiting(w: AffiliateWithdrawal) -> int:
    ref = w.processed_at or _now()
    if not w.created_at:
        return 0
    return max(0, (ref - w.created_at).days)


@router.get("/affiliate/overview")
async def overview(db: Session = Depends(get_db), current_admin: User = Depends(get_current_admin)):
    # Bring the ledger up to date before reporting totals.
    sync_commissions(db)
    total_commission = db.query(func.coalesce(func.sum(AffiliateWalletTx.amount), 0)).filter(
        AffiliateWalletTx.type == "commission").scalar() or 0
    total_paid = db.query(func.coalesce(func.sum(AffiliateWithdrawal.amount), 0)).filter(
        AffiliateWithdrawal.status == "paid").scalar() or 0
    total_balance = db.query(func.coalesce(func.sum(User.affiliate_balance), 0)).scalar() or 0
    pending_count = db.query(func.count(AffiliateWithdrawal.withdrawal_id)).filter(
        AffiliateWithdrawal.status == "pending").scalar() or 0

    # System-wide funnel: link clicks -> signups -> VIP purchases.
    total_clicks = db.query(func.coalesce(func.sum(User.referral_clicks), 0)).scalar() or 0
    total_referred_signups = db.query(func.count(User.user_id)).filter(
        User.referred_by.isnot(None)).scalar() or 0
    total_vip_signups = db.query(func.count(func.distinct(AffiliateWalletTx.source_user_id))).filter(
        AffiliateWalletTx.type == "commission",
        AffiliateWalletTx.source_user_id.isnot(None),
    ).scalar() or 0

    return {
        "total_commission": int(total_commission),
        "total_paid": int(total_paid),
        "total_unwithdrawn": int(total_balance),
        "pending_requests": int(pending_count),
        "total_clicks": int(total_clicks),
        "total_referred_signups": int(total_referred_signups),
        "total_vip_signups": int(total_vip_signups),
        "currency": AFFILIATE_CURRENCY,
        "unit_divisor": UNIT_DIVISOR,
        "withdraw_min": WITHDRAW_MIN_XU,
    }


@router.post("/affiliate/sync")
async def sync_now(db: Session = Depends(get_db), current_admin: User = Depends(get_current_admin)):
    """Credit commission for completed VIP purchases that are not in the ledger yet.
    Idempotent; safe to call from a scheduler."""
    return {"ok": True, "credited": sync_commissions(db, limit=5000)}


@router.get("/affiliate/withdrawals")
async def list_withdrawals(
    status: Optional[str] = Query(None, pattern="^(pending|paid|rejected)$"),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    query = db.query(AffiliateWithdrawal, User).join(User, User.user_id == AffiliateWithdrawal.user_id)
    if status:
        query = query.filter(AffiliateWithdrawal.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.username.like(like), User.email.like(like)))
    rows = query.order_by(AffiliateWithdrawal.withdrawal_id.desc()).all()
    out = []
    for w, u in rows:
        out.append({
            "withdrawal_id": w.withdrawal_id,
            "user_id": u.user_id,
            "username": u.username,
            "email": u.email,
            "balance": int(u.affiliate_balance or 0),
            "amount": int(w.amount),
            "status": w.status,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "processed_at": w.processed_at.isoformat() if w.processed_at else None,
            "days_waiting": _days_waiting(w),
        })
    return out


@router.get("/affiliate/withdrawals/{withdrawal_id}")
async def withdrawal_detail(
    withdrawal_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    w = db.query(AffiliateWithdrawal).filter(AffiliateWithdrawal.withdrawal_id == withdrawal_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu")
    u = db.query(User).filter(User.user_id == w.user_id).first()
    return {
        "withdrawal_id": w.withdrawal_id,
        "user_id": w.user_id,
        "username": u.username if u else None,
        "email": u.email if u else None,
        "account_holder": w.account_holder,
        "account_number": w.account_number,
        "bank": w.bank,
        "qr_url": w.qr_url,
        "amount": int(w.amount),
        "status": w.status,
        "admin_note": w.admin_note,
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "processed_at": w.processed_at.isoformat() if w.processed_at else None,
        "days_waiting": _days_waiting(w),
        "current_balance": int(u.affiliate_balance or 0) if u else 0,
    }


@router.post("/affiliate/withdrawals/{withdrawal_id}/paid")
async def mark_paid(
    withdrawal_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    w = db.query(AffiliateWithdrawal).filter(AffiliateWithdrawal.withdrawal_id == withdrawal_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu")
    if w.status != "pending":
        raise HTTPException(status_code=400, detail="Yêu cầu đã được xử lý")
    w.status = "paid"
    w.processed_at = _now()
    db.commit()
    return {"ok": True, "status": w.status}


class RejectRequest(BaseModel):
    reason: Optional[str] = None


@router.post("/affiliate/withdrawals/{withdrawal_id}/reject")
async def reject(
    withdrawal_id: int,
    payload: Optional[RejectRequest] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Reject a pending request and REFUND the deducted balance to the affiliate.
    The admin's reason is stored (shown to the customer so they can fix & re-request,
    e.g. re-upload a valid QR)."""
    w = db.query(AffiliateWithdrawal).filter(AffiliateWithdrawal.withdrawal_id == withdrawal_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu")
    if w.status != "pending":
        raise HTTPException(status_code=400, detail="Yêu cầu đã được xử lý")
    reason = (payload.reason or "").strip() if payload else ""
    if not reason:
        raise HTTPException(status_code=400, detail="Vui lòng nhập lý do từ chối")
    user = db.query(User).filter(User.user_id == w.user_id).first()
    if user:
        user.affiliate_balance = (user.affiliate_balance or 0) + w.amount
        db.add(AffiliateWalletTx(
            user_id=user.user_id,
            type="withdraw_refund",
            amount=w.amount,
            balance_after=user.affiliate_balance,
            description=f"Refund of withdrawal request #{w.withdrawal_id} (rejected)",
        ))
    w.status = "rejected"
    w.admin_note = reason
    w.processed_at = _now()
    db.commit()
    return {"ok": True, "status": w.status}
