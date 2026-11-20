"""Affiliate discount on the first VIP order (2026-11-14, "đổi chính sách affiliate quốc tế").

Rules:
  * A buyer linked to an affiliator — through the ?ref= link (claim-ref) or by entering
    the affiliator's code at checkout — gets DISCOUNT_PCT off their FIRST VIP order,
    but only while that affiliator's CreatorProfile.discount_enabled is ON (admin
    "Discount Permission", default OFF). With it OFF the link/code still links the
    account and the affiliator still earns commission; the buyer pays full price.
  * "First VIP order" = the account has no completed paid PackageTransaction yet, so
    the discount can only ever be used once, on one order.
  * The code box is offered only to accounts that are NOT linked yet and have never
    bought VIP. A valid code links the account permanently (User.referred_by) when the
    checkout is created. A linked account can never enter a code (no double discount,
    no switching affiliator); an affiliator can't use their own code.
  * Commission keeps being computed on the amount actually paid (the transaction
    amount after the discount) — see app/utils/affiliate.py.

The discount is applied when the checkout is created (vip_packages.py). The payment
webhooks compare the paid amount with that checkout's amount, so they are unchanged.
"""
import os
from typing import Optional

from sqlalchemy import exists

from app.models.models import User, PackageTransaction, CreatorProfile
from app.utils.creator_hub import referral_allowed

DISCOUNT_PCT = float(os.getenv("AFFILIATE_DISCOUNT_PCT", "5"))


class DiscountCodeError(ValueError):
    pass


def has_paid_vip(db, user_id: int) -> bool:
    return bool(db.query(exists().where(
        PackageTransaction.user_id == user_id,
        PackageTransaction.status == "completed",
        PackageTransaction.amount > 0,
    )).scalar())


def discount_enabled_for(db, affiliator_id: Optional[int]) -> bool:
    if not affiliator_id:
        return False
    p = db.query(CreatorProfile).filter(CreatorProfile.user_id == affiliator_id).first()
    return bool(p and p.discount_enabled) and referral_allowed(db, affiliator_id)


def find_code_owner(db, user: User, code: str) -> Optional[User]:
    """The affiliator owning `code`, or None when it can't be used by `user`."""
    code = (code or "").strip()
    if not code or len(code) > 32:
        return None
    owner = db.query(User).filter(User.referral_code == code).first()
    if not owner or owner.user_id == user.user_id or not referral_allowed(db, owner.user_id):
        return None
    return owner


def checkout_offer(db, user: User) -> dict:
    """What the Payment page shows: a discount already earned through the link, or the code box."""
    first = not has_paid_vip(db, user.user_id)
    if user.referred_by:
        pct = DISCOUNT_PCT if first and discount_enabled_for(db, user.referred_by) else 0.0
        return {"discount_pct": pct, "source": "link" if pct else None, "show_code_box": False,
                "default_pct": DISCOUNT_PCT}
    return {"discount_pct": 0.0, "source": None, "show_code_box": first, "default_pct": DISCOUNT_PCT}


def validate_code(db, user: User, code: str) -> dict:
    """Check a code typed on the Payment page (does not link the account yet)."""
    offer = checkout_offer(db, user)
    if not offer["show_code_box"]:
        raise DiscountCodeError("Discount codes can only be used on your first VIP order.")
    owner = find_code_owner(db, user, code)
    if not owner:
        raise DiscountCodeError("This discount code is not valid.")
    pct = DISCOUNT_PCT if discount_enabled_for(db, owner.user_id) else 0.0
    return {"valid": True, "code": owner.referral_code, "discount_pct": pct}


def discount_for_checkout(db, user: User, code: Optional[str]) -> float:
    """Discount (%) for the checkout being created. A valid `code` links the account to
    its affiliator now (permanent). Raises DiscountCodeError for an unusable code."""
    code = (code or "").strip()
    first = not has_paid_vip(db, user.user_id)
    if user.referred_by:
        # Already linked: any typed code is ignored (no second discount, no switching).
        return DISCOUNT_PCT if first and discount_enabled_for(db, user.referred_by) else 0.0
    if not code:
        return 0.0
    if not first:
        raise DiscountCodeError("Discount codes can only be used on your first VIP order.")
    owner = find_code_owner(db, user, code)
    if not owner:
        raise DiscountCodeError("This discount code is not valid.")
    # Link only if still unlinked (guards a concurrent claim-ref).
    db.query(User).filter(User.user_id == user.user_id, User.referred_by.is_(None)) \
        .update({User.referred_by: owner.user_id}, synchronize_session=False)
    db.commit()
    linked_to = db.query(User.referred_by).filter(User.user_id == user.user_id).scalar()
    return DISCOUNT_PCT if discount_enabled_for(db, linked_to) else 0.0


def apply_pct(amount: float, pct: float) -> float:
    return float(amount) * (100.0 - pct) / 100.0 if pct else float(amount)
