"""
9Pay Payment Gateway Service Layer (international cards, redirect flow)
Handles payment URL signing, IPN checksum verification and the status inquiry API.
Docs: https://developers.9pay.vn/thanh-toan-the-quoc-te/redirect
"""
import os
import hmac
import json
import time
import base64
import hashlib
import logging
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

SANDBOX_ENDPOINT = "https://sand-payment.9pay.vn"

# 9Pay transaction statuses (developers.9pay.vn/quy-dinh/thong-tin-ma-loi)
STATUS_SUCCESS = 5
FAILED_STATUSES = {6, 8, 9}   # failed, cancelled, rejected by risk management


def _config():
    merchant_key = os.getenv("NINEPAY_MERCHANT_KEY")
    secret_key = os.getenv("NINEPAY_SECRET_KEY")
    checksum_key = os.getenv("NINEPAY_CHECKSUM_KEY")
    if not all([merchant_key, secret_key, checksum_key]):
        raise ValueError(
            "9Pay credentials not configured. "
            "Set NINEPAY_MERCHANT_KEY, NINEPAY_SECRET_KEY, NINEPAY_CHECKSUM_KEY in .env"
        )
    endpoint = os.getenv("NINEPAY_ENDPOINT", SANDBOX_ENDPOINT).rstrip("/")
    return merchant_key, secret_key, checksum_key, endpoint


def is_configured() -> bool:
    try:
        _config()
        return True
    except ValueError:
        return False


def _sign(message: str, secret_key: str) -> str:
    """base64(HMAC-SHA256(message, secret_key)) — the 9Pay merchant signature."""
    digest = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _http_query(params: dict) -> str:
    """Parameters sorted by name and form-encoded, as the 9Pay samples build them."""
    return urlencode(sorted(params.items()))


def create_payment_url(invoice_no: str, amount: int, description: str, return_url: str,
                       back_url: str = None, lang: str = "en") -> str:
    """Build the signed 9Pay portal URL the customer is redirected to.
    Amount is VND (9Pay limits: 10,000 – 200,000,000)."""
    merchant_key, secret_key, _, endpoint = _config()
    now = int(time.time())
    params = {
        "merchantKey": merchant_key,
        "time": now,
        "invoice_no": invoice_no,
        "amount": int(amount),
        "description": description,
        "return_url": return_url,
        "back_url": back_url or return_url,
        "method": "CREDIT_CARD",
        "currency": "VND",
        "lang": lang,
    }
    message = f"POST\n{endpoint}/payments/create\n{now}\n{_http_query(params)}"
    signature = _sign(message, secret_key)
    base_encode = base64.b64encode(json.dumps(params, separators=(",", ":")).encode()).decode()
    return f"{endpoint}/portal?" + _http_query({"baseEncode": base_encode, "signature": signature})


def verify_checksum(result: str, checksum: str) -> bool:
    """IPN / return data check: UPPER(SHA256(result + checksum_key)) == checksum."""
    _, _, checksum_key, _ = _config()
    if not result or not checksum:
        return False
    expected = hashlib.sha256((result + checksum_key).encode()).hexdigest().upper()
    return hmac.compare_digest(expected, checksum.strip().upper())


def decode_result(result: str) -> dict:
    """`result` is URL-safe base64 JSON, possibly without padding."""
    padded = result + "=" * (-len(result) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode()).decode("utf-8"))


def inquire(invoice_no: str) -> dict:
    """GET /v2/payments/{invoice_no}/inquire — the authoritative transaction status.
    Used when an IPN may have been missed."""
    merchant_key, secret_key, _, endpoint = _config()
    now = int(time.time())
    url = f"{endpoint}/v2/payments/{invoice_no}/inquire"
    signature = _sign(f"GET\n{url}\n{now}", secret_key)
    headers = {
        "Date": str(now),
        "Authorization": f"Signature Algorithm=HS256,Credential={merchant_key},SignedHeaders=,Signature={signature}",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    # Tolerate a {"data": {...}} envelope as well as the flat shape in the docs
    return body.get("data", body) if isinstance(body, dict) else {}
