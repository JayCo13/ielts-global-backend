"""
PayPal Payment Gateway Service Layer
Handles all interactions with PayPal REST API v2 including:
- OAuth2 access token management
- Order creation (checkout)
- Order capture (payment confirmation)
- Webhook signature verification
"""
import os
import time
import hmac
import hashlib
import base64
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

# Token cache
_access_token: Optional[str] = None
_token_expires_at: float = 0


def _get_base_url() -> str:
    """Get PayPal API base URL based on mode (sandbox or live)."""
    mode = os.getenv("PAYPAL_MODE", "sandbox").lower()
    if mode == "live":
        return "https://api-m.paypal.com"
    return "https://api-m.sandbox.paypal.com"


def _get_credentials() -> tuple[str, str]:
    """Get PayPal client credentials from environment."""
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise ValueError(
            "PayPal credentials not configured. "
            "Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env"
        )
    
    return client_id, client_secret


def get_access_token() -> str:
    """
    Get a valid PayPal OAuth2 access token.
    Caches the token and refreshes when expired.
    """
    global _access_token, _token_expires_at
    
    # Return cached token if still valid (with 60s buffer)
    if _access_token and time.time() < (_token_expires_at - 60):
        return _access_token
    
    client_id, client_secret = _get_credentials()
    base_url = _get_base_url()
    
    with httpx.Client() as client:
        response = client.post(
            f"{base_url}/v1/oauth2/token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
    
    _access_token = data["access_token"]
    _token_expires_at = time.time() + data.get("expires_in", 3600)
    logger.info("PayPal access token obtained/refreshed")
    
    return _access_token


def create_order(
    amount_usd: float,
    description: str,
    return_url: str,
    cancel_url: str,
    custom_id: str = "",
) -> dict:
    """
    Create a PayPal order for checkout.
    
    Args:
        amount_usd: Payment amount in USD (e.g., 9.99)
        description: Short description of the purchase
        return_url: URL to redirect after approval
        cancel_url: URL to redirect after cancellation
        custom_id: Custom identifier (e.g., transaction_id) for tracking
    
    Returns:
        dict with 'id' (PayPal order ID) and 'approve_url' (redirect URL)
    """
    token = get_access_token()
    base_url = _get_base_url()
    
    order_data = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {
                "currency_code": "USD",
                "value": f"{amount_usd:.2f}"
            },
            "description": description[:127],  # PayPal max 127 chars
            "custom_id": custom_id,
        }],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                    "brand_name": "IELTS Computer Test",
                    "landing_page": "LOGIN",
                    "user_action": "PAY_NOW",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
            }
        }
    }
    
    with httpx.Client() as client:
        response = client.post(
            f"{base_url}/v2/checkout/orders",
            json=order_data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        response.raise_for_status()
        result = response.json()
    
    # Extract approval URL
    approve_url = None
    for link in result.get("links", []):
        if link.get("rel") == "payer-action":
            approve_url = link["href"]
            break
    
    order_id = result["id"]
    logger.info(f"PayPal order created: {order_id}, amount=${amount_usd:.2f}")
    
    return {
        "id": order_id,
        "status": result.get("status"),
        "approve_url": approve_url,
    }


def capture_order(order_id: str) -> dict:
    """
    Capture a PayPal order after user approval.
    This finalizes the payment and transfers funds.
    
    Args:
        order_id: The PayPal order ID returned from create_order
    
    Returns:
        dict with capture details including status and capture_id
    """
    token = get_access_token()
    base_url = _get_base_url()
    
    with httpx.Client() as client:
        response = client.post(
            f"{base_url}/v2/checkout/orders/{order_id}/capture",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        result = response.json()
    
    status = result.get("status")
    capture_id = None
    
    # Extract capture ID from purchase_units
    purchase_units = result.get("purchase_units", [])
    if purchase_units:
        captures = purchase_units[0].get("payments", {}).get("captures", [])
        if captures:
            capture_id = captures[0].get("id")
    
    logger.info(f"PayPal order captured: {order_id}, status={status}, capture_id={capture_id}")
    
    return {
        "order_id": order_id,
        "status": status,
        "capture_id": capture_id,
        "raw": result,
    }


def verify_webhook_signature(
    headers: dict,
    body: bytes,
    webhook_id: str = None,
) -> bool:
    """
    Verify PayPal webhook signature.
    
    For production, this calls PayPal's verify-webhook-signature endpoint.
    Returns True if the webhook is authentic.
    """
    if not webhook_id:
        webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "")
    
    if not webhook_id:
        logger.warning("PAYPAL_WEBHOOK_ID not set, skipping webhook verification")
        return True  # Skip verification if no webhook ID configured
    
    token = get_access_token()
    base_url = _get_base_url()
    
    verify_data = {
        "auth_algo": headers.get("paypal-auth-algo", ""),
        "cert_url": headers.get("paypal-cert-url", ""),
        "transmission_id": headers.get("paypal-transmission-id", ""),
        "transmission_sig": headers.get("paypal-transmission-sig", ""),
        "transmission_time": headers.get("paypal-transmission-time", ""),
        "webhook_id": webhook_id,
        "webhook_event": __import__("json").loads(body),
    }
    
    with httpx.Client() as client:
        response = client.post(
            f"{base_url}/v1/notifications/verify-webhook-signature",
            json=verify_data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        result = response.json()
    
    is_valid = result.get("verification_status") == "SUCCESS"
    if not is_valid:
        logger.warning(f"PayPal webhook verification failed: {result}")
    
    return is_valid
