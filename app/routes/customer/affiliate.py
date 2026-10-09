"""Customer-facing affiliate endpoints (Profile → Affiliate), ported from the VN tree.

Each customer sees their referral link, signup count, total commission earned,
wallet balance + history, and can request a withdrawal once the balance reaches the
minimum. Commission is credited by reconciliation (see app/utils/affiliate.py).
"""
import os
import uuid
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import User, AffiliateWalletTx, AffiliateWithdrawal
from app.routes.admin.auth import get_current_student
from app.utils.affiliate import (
    ensure_referral_code, sync_commissions, format_amount,
    WITHDRAW_MIN_XU, AFFILIATE_CURRENCY, UNIT_DIVISOR, COMMISSION_RATE,
)
from app.utils.upload_security import validate_image_bytes
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://englishoncomputer.com").rstrip("/")


def _notify_admin_withdrawal(username: str, email: str, amount: int, bank: str, account_number: str, account_holder: str, has_qr: bool):
    """Best-effort email to the admin inbox when a withdrawal is requested."""
    try:
        from app.utils.email_utils import send_email, EMAIL_FROM, EMAIL_USERNAME
        to = os.getenv("ADMIN_NOTIFY_EMAIL") or EMAIL_FROM or EMAIL_USERNAME
        if not to:
            return
        body = (
            '<div style="font-family:Arial,sans-serif;line-height:1.6;color:#333;max-width:600px;margin:0 auto;padding:20px;">'
            '<h2 style="color:#0096b1;">New affiliate withdrawal request</h2>'
            f"<p>Account: <b>{username}</b> ({email or '—'})</p>"
            f"<p>Amount: <b>{format_amount(amount)}</b></p>"
            f"<p>Pay to: {'QR + ' if has_qr else ''}{bank or '—'} · {account_number or '—'} · {account_holder or '—'}</p>"
            "<p>Open Admin → Affiliate to review and process it.</p>"
            "</div>"
        )
        send_email(to, "New affiliate withdrawal request", body)
    except Exception:
        pass


def _now():
    return get_vietnam_time().replace(tzinfo=None)


def _mask_name(username: Optional[str], email: Optional[str]) -> str:
    """Mask the buyer's account name for the affiliate's history (e.g. 'Mai***')."""
    name = (username or "").strip() or (email or "").split("@")[0].strip()
    if not name:
        return "Anonymous"
    if len(name) <= 3:
        return name + "***"
    return name[:3] + "***"


@router.get("")
@router.get("/")
async def get_affiliate(db: Session = Depends(get_db), current: User = Depends(get_current_student)):
    # Lazily assign a referral code to legacy accounts that don't have one.
    if not current.referral_code:
        ensure_referral_code(db, current)
        db.commit()
        db.refresh(current)

    # Credit any completed purchases by my referrals that aren't in the ledger yet.
    if sync_commissions(db, referrer_id=current.user_id):
        db.refresh(current)

    # Accounts registered through this user's link (referred_by = me).
    signup_count = db.query(func.count(User.user_id)).filter(User.referred_by == current.user_id).scalar() or 0
    # Referred accounts that have actually bought VIP (distinct buyers with commission).
    vip_signup_count = db.query(func.count(func.distinct(AffiliateWalletTx.source_user_id))).filter(
        AffiliateWalletTx.user_id == current.user_id,
        AffiliateWalletTx.type == "commission",
        AffiliateWalletTx.source_user_id.isnot(None),
    ).scalar() or 0
    total_commission = db.query(func.coalesce(func.sum(AffiliateWalletTx.amount), 0)).filter(
        AffiliateWalletTx.user_id == current.user_id,
        AffiliateWalletTx.type == "commission",
    ).scalar() or 0
    balance = current.affiliate_balance or 0
    return {
        "referral_code": current.referral_code,
        "referral_link": f"{FRONTEND_URL}/?ref={current.referral_code}",
        "click_count": int(current.referral_clicks or 0),
        "signup_count": int(signup_count),
        "vip_signup_count": int(vip_signup_count),
        "total_commission": int(total_commission),
        "balance": int(balance),
        "withdraw_min": WITHDRAW_MIN_XU,
        "can_withdraw": int(balance) >= WITHDRAW_MIN_XU,
        # Amounts are integers in the currency's minor unit; divide by unit_divisor to display.
        "currency": AFFILIATE_CURRENCY,
        "unit_divisor": UNIT_DIVISOR,
        "commission_rate": COMMISSION_RATE,
    }


class TrackClick(BaseModel):
    ref: str = Field(..., max_length=32)


@router.post("/track-click")
async def track_click(payload: TrackClick, db: Session = Depends(get_db)):
    """Public: count a visit landing via a referral link (?ref=code). Best-effort,
    de-duplicated per browser on the frontend, rate-limited at the edge (nginx).
    Always returns the same response so it can't be used as a referral-code oracle."""
    try:
        code = (payload.ref or "").strip()
        if code:
            owner = db.query(User).filter(User.referral_code == code).first()
            if owner:
                owner.referral_clicks = (owner.referral_clicks or 0) + 1
                db.commit()
        # Uniform response regardless of validity (no enumeration oracle).
        return {"ok": True}
    except Exception:
        return {"ok": True}


CLAIM_WINDOW_MIN = 15


@router.post("/claim-ref")
async def claim_ref(payload: TrackClick, db: Session = Depends(get_db),
                    current: User = Depends(get_current_student)):
    """Attach a freshly registered account to the affiliate whose link it came through.

    The frontend keeps the `?ref=` code in localStorage and sends it here right after
    login/registration; the backend decides whether to accept it. In global this is
    the ONLY attribution path (the auth routes are not modified).

    Four guards, because this touches money:
      1. Only within CLAIM_WINDOW_MIN minutes of account creation — otherwise an old
         user could hand their purchases to a friend just by opening the friend's link.
      2. Only while `referred_by` is empty — never steals another affiliate's referral.
      3. No self-referral.
      4. A wrong code is ignored silently with the same response, so codes can't be probed.
    """
    try:
        code = (payload.ref or "").strip()
        if not code or current.referred_by:
            return {"ok": True}
        created = current.created_at
        if not created or (_now() - created).total_seconds() > CLAIM_WINDOW_MIN * 60:
            return {"ok": True}
        owner = db.query(User).filter(User.referral_code == code).first()
        if owner and owner.user_id != current.user_id:
            current.referred_by = owner.user_id
            db.commit()
        return {"ok": True}
    except Exception:
        db.rollback()
        return {"ok": True}


@router.get("/history")
async def affiliate_history(db: Session = Depends(get_db), current: User = Depends(get_current_student)):
    rows = (
        db.query(AffiliateWalletTx)
        .filter(AffiliateWalletTx.user_id == current.user_id)
        .order_by(AffiliateWalletTx.id.desc())
        .limit(200)
        .all()
    )
    # Map source_user_id -> masked buyer name so the affiliate can track who bought.
    buyer_ids = {r.source_user_id for r in rows if r.source_user_id}
    buyer_map = {}
    if buyer_ids:
        for u in db.query(User).filter(User.user_id.in_(buyer_ids)).all():
            buyer_map[u.user_id] = _mask_name(u.username, u.email)
    return [
        {
            "id": r.id,
            "type": r.type,
            "amount": r.amount,
            "balance_after": r.balance_after,
            "description": r.description,
            "source": buyer_map.get(r.source_user_id) if r.source_user_id else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/withdrawals")
async def my_withdrawals(db: Session = Depends(get_db), current: User = Depends(get_current_student)):
    rows = (
        db.query(AffiliateWithdrawal)
        .filter(AffiliateWithdrawal.user_id == current.user_id)
        .order_by(AffiliateWithdrawal.withdrawal_id.desc())
        .all()
    )
    return [
        {
            "withdrawal_id": w.withdrawal_id,
            "amount": w.amount,
            "status": w.status,
            "bank": w.bank,
            "account_number": w.account_number,
            "account_holder": w.account_holder,
            "reject_reason": w.admin_note if w.status == "rejected" else None,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "processed_at": w.processed_at.isoformat() if w.processed_at else None,
        }
        for w in rows
    ]


# ── payout method (Payment page) ─────────────────────────────────────────────



def _has_payout(user: User) -> bool:
    """User can withdraw once they've provided a QR image OR full bank details."""
    has_bank = bool((user.payout_bank or "").strip() and (user.payout_account_number or "").strip()
                    and (user.payout_account_holder or "").strip())
    return bool(user.payout_qr_url) or has_bank


@router.get("/payment")
async def get_payment(current: User = Depends(get_current_student)):
    return {
        "qr_url": current.payout_qr_url,
        "bank": current.payout_bank,
        "account_number": current.payout_account_number,
        "account_holder": current.payout_account_holder,
        "is_set": _has_payout(current),
    }


class PaymentInfo(BaseModel):
    bank: Optional[str] = None
    account_number: Optional[str] = None
    account_holder: Optional[str] = None


@router.put("/payment")
async def set_payment(
    payload: PaymentInfo,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_student),
):
    current.payout_bank = (payload.bank or "").strip() or None
    current.payout_account_number = (payload.account_number or "").strip() or None
    current.payout_account_holder = (payload.account_holder or "").strip() or None
    db.commit()
    return {"ok": True, "is_set": _has_payout(current)}


@router.post("/payment/qr")
async def upload_payout_qr(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_student),
):
    data = await image.read()
    ext = validate_image_bytes(data)   # magic-byte + size check; safe extension
    # Koyeb's disk is ephemeral: keep the QR in R2 (absolute URL), like other uploads.
    from app.utils.r2_storage import upload_image_to_r2
    content_type = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
                    ".webp": "image/webp", ".bmp": "image/bmp"}.get(ext, "application/octet-stream")
    try:
        current.payout_qr_url = upload_image_to_r2(data, f"affiliate_qr/{uuid.uuid4().hex}{ext}", content_type)
    except Exception:
        raise HTTPException(status_code=502, detail="Image upload failed. Please try again.")
    db.commit()
    return {"ok": True, "qr_url": current.payout_qr_url}


@router.delete("/payment/qr")
async def delete_payout_qr(db: Session = Depends(get_db), current: User = Depends(get_current_student)):
    current.payout_qr_url = None
    db.commit()
    return {"ok": True}


# ── withdrawal ───────────────────────────────────────────────────────────────

class WithdrawRequest(BaseModel):
    amount: Optional[int] = None  # wallet minor units; default = full balance


@router.post("/withdraw")
async def request_withdraw(
    payload: WithdrawRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_student),
):
    if not _has_payout(current):
        raise HTTPException(status_code=400, detail="Please add your payout details (QR code or bank / PayPal account) before withdrawing")
    # Lock the user row so two concurrent /withdraw calls can't both pass the
    # balance check and double-spend (TOCTOU). Re-read balance under the lock.
    locked = db.query(User).filter(User.user_id == current.user_id).with_for_update().first()
    balance = (locked.affiliate_balance if locked else current.affiliate_balance) or 0
    if balance < WITHDRAW_MIN_XU:
        raise HTTPException(status_code=400, detail=f"The minimum balance to withdraw is {format_amount(WITHDRAW_MIN_XU)}")
    amount = payload.amount if payload.amount and payload.amount > 0 else balance
    if amount < WITHDRAW_MIN_XU:
        raise HTTPException(status_code=400, detail=f"The minimum withdrawal amount is {format_amount(WITHDRAW_MIN_XU)}")
    if amount > balance:
        raise HTTPException(status_code=400, detail="The amount exceeds your balance")

    # Deduct immediately; refunded if the admin later rejects. Snapshot the saved
    # payout method (QR + bank) so the admin can just scan and transfer.
    locked.affiliate_balance = balance - amount
    db.add(AffiliateWalletTx(
        user_id=current.user_id,
        type="withdraw",
        amount=-amount,
        balance_after=locked.affiliate_balance,
        description="Withdrawal request",
    ))
    w = AffiliateWithdrawal(
        user_id=current.user_id,
        amount=amount,
        account_holder=current.payout_account_holder,
        account_number=current.payout_account_number,
        bank=current.payout_bank,
        qr_url=current.payout_qr_url,
        status="pending",
    )
    db.add(w)
    db.commit()
    db.refresh(w)

    # Notify the admin by email (after the response, non-blocking).
    background_tasks.add_task(
        _notify_admin_withdrawal, current.username, current.email, int(amount),
        current.payout_bank, current.payout_account_number, current.payout_account_holder,
        bool(current.payout_qr_url),
    )
    return {"ok": True, "withdrawal_id": w.withdrawal_id, "status": w.status, "balance": locked.affiliate_balance}
