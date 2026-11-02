"""Pre-send filtering of broadcast recipients, so marketing sends are not wasted on
junk sign-ups (registration accepts any address; verification is optional).

Always removed: bad format, disposable providers, placeholder local parts
(noreply@, test@ …), duplicates of the same inbox (Gmail ignores dots and +tags),
domains with no mail server, and the suppression list (unsubscribed, hard bounced,
marked as spam — see utils/email_suppression.py).

With only_trusted, an address is kept only when there is a sign the inbox is real:
email verified, Google sign-in, a completed purchase, a taken test, or recent activity
on a day after sign-up (sign-up itself sets last_active, so it does not count).

Recipients come out most engaged first (verified, Google, buyers, then test-takers,
then the rest, each by recent activity), so a broadcast limited to the first N
reaches the inboxes least likely to bounce or complain.
"""
import re
from datetime import timedelta
from functools import lru_cache

import dns.exception
import dns.resolver
from sqlalchemy import and_

from app.models.models import User, PackageTransaction, ExamResult
from app.utils.email_blocklist import is_disposable_email
from app.utils.datetime_utils import get_vietnam_time
from app.utils.email_suppression import suppressed_emails

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

ACTIVE_WITHIN_DAYS = 180

PLACEHOLDER_LOCAL_PARTS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "test", "testing", "tester",
    "example", "sample", "demo", "fake", "null", "none", "abc", "asdf", "qwerty", "xxx",
}

# Big providers always have mail servers; skip the DNS lookup for them
KNOWN_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.com.vn", "hotmail.com",
    "outlook.com", "live.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
}


@lru_cache(maxsize=5000)
def _domain_receives_mail(domain: str) -> bool:
    if domain in KNOWN_MAIL_DOMAINS:
        return True
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 3.0
    for record in ("MX", "A"):
        try:
            resolver.resolve(domain, record)
            return True
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            continue
        except dns.exception.Timeout:
            return True   # don't drop an address because DNS was slow
        except Exception:
            return True
    return False


def _inbox_key(email: str) -> str:
    """Same key for addresses that land in the same inbox."""
    local, domain = email.rsplit("@", 1)
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def build_recipients(db, target_filter: str = "non_vip", only_trusted: bool = True):
    """Return (emails, stats) for a broadcast. stats counts what each filter removed."""
    query = db.query(
        User.user_id, User.email, User.email_verified, User.google_id, User.last_active,
        User.created_at
    ).filter(User.is_active == True, User.email.isnot(None), User.email != '')
    if target_filter == 'non_vip':
        query = query.filter(User.is_vip == False)
    elif target_filter == 'vip':
        query = query.filter(User.is_vip == True)
    rows = query.order_by(User.user_id).all()

    buyer_ids = {r[0] for r in db.query(PackageTransaction.user_id).filter(
        PackageTransaction.status == "completed").distinct().all()}
    taker_ids = {r[0] for r in db.query(ExamResult.user_id).distinct().all()}
    trusted_ids = (buyer_ids | taker_ids) if only_trusted else set()
    suppressed = suppressed_emails(db)
    active_since = get_vietnam_time().replace(tzinfo=None) - timedelta(days=ACTIVE_WITHIN_DAYS)

    def engagement(row):
        user_id, _, verified, google_id, last_active, _ = row
        tier = 0 if (verified or google_id or user_id in buyer_ids) else 1 if user_id in taker_ids else 2
        return (tier, -(last_active.timestamp() if last_active else 0), user_id)
    rows.sort(key=engagement)

    stats = {"total": len(rows), "bad_format": 0, "disposable": 0, "placeholder": 0,
             "suppressed": 0, "duplicate": 0, "no_mail_server": 0, "untrusted": 0}
    emails, seen = [], set()
    for user_id, raw_email, verified, google_id, last_active, created_at in rows:
        email = raw_email.strip().lower()
        if not EMAIL_REGEX.match(email):
            stats["bad_format"] += 1
            continue
        if email in suppressed:
            stats["suppressed"] += 1
            continue
        if is_disposable_email(email):
            stats["disposable"] += 1
            continue
        local, domain = email.rsplit("@", 1)
        if local.split("+", 1)[0] in PLACEHOLDER_LOCAL_PARTS:
            stats["placeholder"] += 1
            continue
        if only_trusted and not (
            verified or google_id or user_id in trusted_ids
            or (last_active is not None and last_active >= active_since
                and (created_at is None or last_active - created_at >= timedelta(days=1)))
        ):
            stats["untrusted"] += 1
            continue
        key = _inbox_key(email)
        if key in seen:
            stats["duplicate"] += 1
            continue
        if not _domain_receives_mail(domain):
            stats["no_mail_server"] += 1
            continue
        seen.add(key)
        emails.append(email)

    stats["final"] = len(emails)
    return emails, stats
