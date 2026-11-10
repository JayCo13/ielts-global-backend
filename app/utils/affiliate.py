"""Affiliate / referral helpers (ported from the Vietnam tree).

Commission = rate% (default 5%, per-account override; see rate_pct_at) of the amount actually paid for a VIP PackageTransaction, credited
to the referrer's wallet PERMANENTLY (every purchase by a referred user). Credit is
idempotent via the unique AffiliateWalletTx.source_transaction_id.

Global differences from VN:
  * Commission is credited by RECONCILIATION (`sync_commissions`) instead of hooks
    inside the payment webhooks: completed transactions of referred buyers that have
    no ledger row yet are credited when the affiliate opens their page, when the
    admin opens the affiliate pages, or via POST /admin/affiliate/sync. The payment
    and auth code paths are left untouched.
  * The wallet has ONE currency (AFFILIATE_CURRENCY, default VND). Amounts are stored
    as integers in that currency's minor unit (VND: 1 = 1 VND; USD: 1 = 1 cent).
  * A USD wallet converts VND payments (PayOS / bank transfer) at the site's fixed
    display rate (USD_VND_RATE), so commission matches the USD price the buyer saw.
    A VND wallet still skips USD payments.
"""
import os
import random
import string

from app.models.models import User, AffiliateWalletTx, PackageTransaction, AffiliateRateChange
from app.utils.currency import vnd_to_usd
from app.utils.datetime_utils import get_vietnam_time

COMMISSION_RATE = float(os.getenv("AFFILIATE_COMMISSION_RATE", "0.20"))
AFFILIATE_CURRENCY = (os.getenv("AFFILIATE_CURRENCY", "VND") or "VND").upper()
# Minor units per 1 unit of currency: VND has none, USD has cents.
UNIT_DIVISOR = 100 if AFFILIATE_CURRENCY == "USD" else 1
_DEFAULT_MIN = "1000" if AFFILIATE_CURRENCY == "USD" else "300000"   # 10.00 USD / 300,000 VND
WITHDRAW_MIN_XU = int(os.getenv("AFFILIATE_WITHDRAW_MIN", _DEFAULT_MIN))
_ALPHABET = string.ascii_uppercase + string.digits

# Per-account rates (2026-11, ported from VN): the rate is versioned in
# AffiliateRateChange and looked up at the order's created_at. COMMISSION_RATE above
# is only the fallback while that table has no default row.
DEFAULT_RATE_PCT = COMMISSION_RATE * 100.0


def _now():
    return get_vietnam_time().replace(tzinfo=None)


_rates_table_ok = None


def _rates_table_exists(db) -> bool:
    """True once affiliate_rate_changes exists (checked without touching the
    session's transaction, so a missing table never rolls back pending credits)."""
    global _rates_table_ok
    if _rates_table_ok:
        return True
    try:
        from sqlalchemy import inspect
        _rates_table_ok = inspect(db.get_bind()).has_table('affiliate_rate_changes')
    except Exception:
        _rates_table_ok = False
    return _rates_table_ok


def default_rate_at(db, at=None) -> float:
    at = at or _now()
    if not _rates_table_exists(db):   # migration not applied yet
        return DEFAULT_RATE_PCT
    row = (db.query(AffiliateRateChange)
           .filter(AffiliateRateChange.user_id.is_(None), AffiliateRateChange.effective_at <= at,
                   AffiliateRateChange.rate_pct.isnot(None))
           .order_by(AffiliateRateChange.effective_at.desc(), AffiliateRateChange.id.desc()).first())
    return float(row.rate_pct) if row else DEFAULT_RATE_PCT


def custom_rate_at(db, user_id, at=None):
    """Admin-set rate for this account at `at` (None = follows the default)."""
    at = at or _now()
    if not _rates_table_exists(db):
        return None
    row = (db.query(AffiliateRateChange)
           .filter(AffiliateRateChange.user_id == user_id, AffiliateRateChange.effective_at <= at)
           .order_by(AffiliateRateChange.effective_at.desc(), AffiliateRateChange.id.desc()).first())
    return float(row.rate_pct) if row and row.rate_pct is not None else None


def rate_pct_at(db, user_id, at=None) -> float:
    """Commission rate (%) of affiliate `user_id` for an order created at `at`."""
    custom = custom_rate_at(db, user_id, at)
    return custom if custom is not None else default_rate_at(db, at)


def fmt_pct(pct: float) -> str:
    return f"{pct:g}%"


def format_amount(units: int) -> str:
    """Human-readable wallet amount, e.g. '300,000 VND' or '$10.00'."""
    units = int(units or 0)
    if UNIT_DIVISOR == 1:
        return f"{units:,} {AFFILIATE_CURRENCY}"
    if AFFILIATE_CURRENCY == "USD":
        return f"${units / UNIT_DIVISOR:,.2f}"
    return f"{units / UNIT_DIVISOR:,.2f} {AFFILIATE_CURRENCY}"


def generate_referral_code(db, length: int = 8) -> str:
    for _ in range(20):
        code = ''.join(random.choices(_ALPHABET, k=length))
        if not db.query(User).filter(User.referral_code == code).first():
            return code
    return ''.join(random.choices(_ALPHABET, k=length + 4))


def ensure_referral_code(db, user: User) -> str:
    """Assign a referral code to a user if they don't have one yet (does not commit)."""
    if not user.referral_code:
        user.referral_code = generate_referral_code(db)
    return user.referral_code


def transaction_currency(transaction) -> str:
    """Currency a PackageTransaction was paid in. Lemon Squeezy orders are USD;
    PayOS / manual bank transfers are VND."""
    method = (getattr(transaction, "payment_method", "") or "").lower()
    if getattr(transaction, "ls_order_id", None) or "lemon" in method:
        return "USD"
    return "VND"


def amount_in_wallet_currency(transaction):
    """transaction.amount expressed in the wallet currency (major units), or None
    when it cannot be credited to this wallet."""
    paid_in = transaction_currency(transaction)
    if paid_in == AFFILIATE_CURRENCY:
        return float(transaction.amount)
    if paid_in == "VND" and AFFILIATE_CURRENCY == "USD":
        return vnd_to_usd(transaction.amount)
    return None


def credit_referral_commission(db, transaction) -> bool:
    """Idempotently credit the buyer's referrer rate% of transaction.amount.
    Does NOT commit — the caller's own db.commit() persists it. Returns True only
    when a new commission ledger row was added."""
    try:
        if not transaction or transaction.amount is None:
            return False
        paid = amount_in_wallet_currency(transaction)
        if paid is None:
            return False
        buyer = db.query(User).filter(User.user_id == transaction.user_id).first()
        if not buyer or not buyer.referred_by or buyer.referred_by == buyer.user_id:
            return False
        # Already credited for this transaction? (also protected by a unique index)
        existing = db.query(AffiliateWalletTx).filter(
            AffiliateWalletTx.source_transaction_id == transaction.transaction_id).first()
        if existing:
            return False
        referrer = db.query(User).filter(User.user_id == buyer.referred_by).first()
        if not referrer:
            return False
        pct = rate_pct_at(db, referrer.user_id, getattr(transaction, 'created_at', None))
        commission = int(round(paid * UNIT_DIVISOR * pct / 100.0))
        if commission <= 0:
            return False
        referrer.affiliate_balance = (referrer.affiliate_balance or 0) + commission
        db.add(AffiliateWalletTx(
            user_id=referrer.user_id,
            type='commission',
            amount=commission,
            balance_after=referrer.affiliate_balance,
            description=f"{fmt_pct(pct)} commission from VIP order #{transaction.transaction_id}",
            source_user_id=buyer.user_id,
            source_transaction_id=transaction.transaction_id,
        ))
        return True
    except Exception:
        return False


def sync_commissions(db, referrer_id: int = None, limit: int = 500) -> int:
    """Credit every completed VIP transaction of a referred buyer that has no
    commission ledger row yet. Idempotent and safe to call often. Commits once at
    the end when anything was credited. Returns the number of new credits.

    Only purchases made at/after the buyer's signup count, and a buyer is only ever
    attached to a referrer within minutes of signing up (see /claim-ref), so this
    cannot pay for purchases that predate the referral.
    """
    try:
        q = (
            db.query(PackageTransaction)
            .join(User, User.user_id == PackageTransaction.user_id)
            .outerjoin(AffiliateWalletTx,
                       AffiliateWalletTx.source_transaction_id == PackageTransaction.transaction_id)
            .filter(
                PackageTransaction.status == "completed",
                User.referred_by.isnot(None),
                AffiliateWalletTx.id.is_(None),
                PackageTransaction.amount > 0,
            )
        )
        # Same currency rule as transaction_currency(), applied in SQL so skipped
        # transactions don't pile up at the head of the scan.
        # A USD wallet takes every payment (VND ones are converted); a VND wallet
        # only takes VND ones.
        if AFFILIATE_CURRENCY != "USD":
            q = q.filter(PackageTransaction.ls_order_id.is_(None))
        if referrer_id is not None:
            q = q.filter(User.referred_by == referrer_id)
        credited = 0
        for tx in q.order_by(PackageTransaction.transaction_id).limit(limit).all():
            if credit_referral_commission(db, tx):
                db.flush()
                credited += 1
        if credited:
            db.commit()
        return credited
    except Exception:
        db.rollback()
        return 0
