"""Post-login email verification codes (6 digits), as on the VN site.

VN keeps the code in Redis. Global may run without Redis, so the code is derived
instead of stored: HMAC(SECRET_KEY, user_id:email:window) over 10-minute windows,
accepting the current and the previous window. Nothing to persist, no schema change.

Throttling (resend cooldown, daily send cap, wrong-attempt cap) is kept in process
memory. With several workers each keeps its own counters, which still leaves a
brute force of a 6-digit code hopeless.
"""
import hashlib
import hmac
import threading
import time

from app.routes.admin.auth import SECRET_KEY

CODE_WINDOW_SECONDS = 600      # a code is valid for 10–20 minutes
RESEND_COOLDOWN_SECONDS = 60   # min seconds between sends for one account
MAX_SENDS_PER_DAY = 10         # per account, protects the email quota
MAX_WRONG_ATTEMPTS = 5         # per account per code window

_lock = threading.Lock()
_last_sent = {}                # user_id -> timestamp of last send
_sends_today = {}              # (user_id, day) -> count
_wrong_attempts = {}           # (user_id, window) -> count


def _window(now: float = None) -> int:
    return int((now or time.time()) // CODE_WINDOW_SECONDS)


def _code_for(user_id: int, email: str, window: int) -> str:
    msg = f"email-verify:{user_id}:{email.strip().lower()}:{window}".encode()
    digest = hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()
    return f"{int(digest, 16) % 1000000:06d}"


def issue_code(user_id: int, email: str) -> str:
    """Code to email to the user. Raises ValueError with a user-facing message when throttled."""
    now = time.time()
    day = int(now // 86400)
    with _lock:
        last = _last_sent.get(user_id)
        if last and now - last < RESEND_COOLDOWN_SECONDS:
            raise ValueError("Please wait a minute before requesting another code.")
        if _sends_today.get((user_id, day), 0) >= MAX_SENDS_PER_DAY:
            raise ValueError("Too many codes requested today. Please try again tomorrow.")
        _last_sent[user_id] = now
        _sends_today[(user_id, day)] = _sends_today.get((user_id, day), 0) + 1
        # Drop counters from previous days so the dicts stay small
        for key in [k for k in _sends_today if k[1] < day]:
            del _sends_today[key]
    return _code_for(user_id, email, _window(now))


def check_code(user_id: int, email: str, code: str) -> bool:
    """True when the code matches the current or previous window. Raises ValueError
    with a user-facing message after too many wrong attempts."""
    code = (code or "").strip()
    window = _window()
    with _lock:
        if _wrong_attempts.get((user_id, window), 0) >= MAX_WRONG_ATTEMPTS:
            raise ValueError("Too many incorrect attempts. Please request a new code in a few minutes.")
    for w in (window, window - 1):
        if hmac.compare_digest(_code_for(user_id, email, w), code):
            return True
    with _lock:
        _wrong_attempts[(user_id, window)] = _wrong_attempts.get((user_id, window), 0) + 1
        for key in [k for k in _wrong_attempts if k[1] < window - 1]:
            del _wrong_attempts[key]
    return False
