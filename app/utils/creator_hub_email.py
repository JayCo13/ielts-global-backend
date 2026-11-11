"""Creator Hub transactional emails (application / access / channel / reconciliation
decisions). Sent in the background AFTER the admin action has committed; a failed
send is logged and never affects the action itself. Transactional, so the marketing
suppression list does not apply.

Env: FRONTEND_URL (link to the hub), CREATOR_HUB_EMAILS=0 to switch all of these off.
"""
import html
import logging
import os

from app.utils.creator_hub import PLATFORMS

logger = logging.getLogger(__name__)

SITE_NAME = os.getenv("SITE_NAME", "EnglishOnComputer")


def _enabled() -> bool:
    return os.getenv("CREATOR_HUB_EMAILS", "1") != "0"


def _hub_url() -> str:
    base = os.getenv("FRONTEND_URL", "https://englishoncomputer.com").rstrip("/")
    return f"{base}/profile?tab=creator-hub"


def _e(s) -> str:
    return html.escape(str(s or ""))


def _layout(name: str, title: str, paragraphs: list, button: str = "Open Creator Hub") -> str:
    body = "".join(f'<p style="margin:0 0 14px">{p}</p>' for p in paragraphs)
    return f"""
<html><body style="margin:0;padding:24px;background:#f5f7fb;font-family:Arial,sans-serif;color:#1f2937;line-height:1.6">
  <div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:28px">
    <h2 style="margin:0 0 18px;color:#14213d;font-size:20px">{_e(title)}</h2>
    <p style="margin:0 0 14px">Hi {_e(name) or 'there'},</p>
    {body}
    <p style="margin:22px 0">
      <a href="{_hub_url()}" style="display:inline-block;padding:11px 22px;background:#1d4ed8;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold">{_e(button)}</a>
    </p>
    <p style="margin:0;color:#6b7280;font-size:13px">Questions? Just reply to this email.<br>The {SITE_NAME} Creator Team</p>
  </div>
</body></html>"""


def _reason_block(reason) -> str:
    return (f'<span style="display:block;padding:10px 14px;background:#fef2f2;border-left:3px solid #dc2626;'
            f'border-radius:4px"><b>Reason:</b> {_e(reason)}</span>')


def _send(to: str, subject: str, html_body: str) -> None:
    if not _enabled() or not to:
        return
    try:
        from app.utils.email_utils import send_email
        if not send_email(to, subject, html_body):
            logger.warning("creator hub email not sent to %s (%s)", to, subject)
    except Exception as exc:  # never let email break an admin action
        logger.warning("creator hub email failed to %s (%s): %s", to, subject, exc)


# ── Public helpers (call via BackgroundTasks.add_task) ───────────────────────

def application_approved(to, name):
    _send(to, f"Welcome to the {SITE_NAME} Creator Program!", _layout(name, "Your application is approved 🎉", [
        "Great news: your Creator Program application has been approved and your <b>Creator Hub</b> is now open.",
        "Next steps: register your channels, wait for channel approval, then submit your views to earn "
        "rewards. Your affiliate link is also available in the hub.",
    ], "Go to Creator Hub"))


def application_rejected(to, name, reason):
    _send(to, f"Your {SITE_NAME} Creator Program application", _layout(name, "Application update", [
        "Thank you for applying to our Creator Program. Unfortunately, we can't approve your application right now.",
        _reason_block(reason),
        "You're welcome to update your details and apply again from your account.",
    ], "View application"))


def access_changed(to, name, enabled, note=None):
    if enabled:
        paras = ["Your <b>Creator Hub</b> access has been enabled. You can now register channels, submit views "
                 "and use your affiliate link."]
        title, subject = "Creator Hub enabled", f"Your {SITE_NAME} Creator Hub is now open"
    else:
        paras = ["Your Creator Hub access has been disabled. Your wallet balance and history are kept."]
        if note:
            paras.append(_reason_block(note))
        paras.append("If you think this is a mistake, please reply to this email.")
        title, subject = "Creator Hub disabled", f"Your {SITE_NAME} Creator Hub access has changed"
    _send(to, subject, _layout(name, title, paras))


_CHANNEL_COPY = {
    "approved": ("Channel approved ✅", "is approved. You can now submit its views for rewards."),
    "rejected": ("Channel not approved", "could not be approved."),
    "suspended": ("Channel suspended", "has been suspended. Views from this channel can't be submitted for now."),
    "reactivated": ("Channel reactivated ✅", "is active again. You can submit its views for rewards."),
}


def channel_status(to, name, platform, channel_name, kind, reason=None):
    title, text = _CHANNEL_COPY[kind]
    label = f"<b>{_e(channel_name)}</b> ({_e(PLATFORMS.get(platform, platform))})"
    paras = [f"Your channel {label} {text}"]
    if reason and kind in ("rejected", "suspended"):
        paras.append(_reason_block(reason))
    if kind == "rejected":
        paras.append("You can register the channel again after fixing the issue.")
    _send(to, f"{title} · {SITE_NAME} Creator Hub", _layout(name, title, paras))


def reconciliation_approved(to, name, rid, usd, amount_text):
    _send(to, f"Your views reward is in your wallet · {SITE_NAME}", _layout(name, "Views approved 💰", [
        f"Your views submission <b>#{_e(rid)}</b> has been approved.",
        f"<b>{_e(amount_text)}</b>{'' if str(amount_text).startswith('$') else f' (${usd:,.2f})'} "
        "has been added to your Creator Hub wallet.",
    ], "View wallet"))


def reconciliation_rejected(to, name, rid, reason):
    _send(to, f"Views submission #{rid} not approved · {SITE_NAME}", _layout(name, "Views submission not approved", [
        f"Your views submission <b>#{_e(rid)}</b> could not be approved.",
        _reason_block(reason),
        "No reward was added for this submission. You can submit again with corrected numbers.",
    ], "Submit again"))


# ── Wallet withdrawals (feedback 2026-11-10: email the creator once the admin has paid) ──

def withdrawal_paid(to, name, wid, amount_text, paid_to=None):
    paras = [f"Your withdrawal request <b>#{_e(wid)}</b> has been paid.",
             f"Amount: <b>{_e(amount_text)}</b>"
             + (f"<br>Sent to: <b>{_e(paid_to)}</b>" if paid_to else "")]
    paras.append("Depending on your payment provider, it may take a little while to show up in your account. "
                 "If you don't see it within 3 business days, just reply to this email.")
    _send(to, f"Your withdrawal has been paid · {SITE_NAME}", _layout(name, "Withdrawal paid 💸", paras, "View wallet"))


def withdrawal_rejected(to, name, wid, amount_text, reason):
    _send(to, f"Withdrawal request #{wid} not processed · {SITE_NAME}", _layout(name, "Withdrawal request not processed", [
        f"We couldn't process your withdrawal request <b>#{_e(wid)}</b>.",
        _reason_block(reason),
        f"<b>{_e(amount_text)}</b> has been returned to your wallet. Please check your payout details "
        "and send a new request.",
    ], "View wallet"))
