"""
Lemon Squeezy Payment Gateway Service Layer
Handles all interactions with Lemon Squeezy API v1 including:
- Checkout session creation (subscription mode)
- Webhook signature verification (HMAC-SHA256)
- Subscription management (cancel, get portal URL)
"""
import os
import hmac
import hashlib
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

LS_API_BASE = "https://api.lemonsqueezy.com"


def _get_api_key() -> str:
    """Get Lemon Squeezy API key from environment."""
    api_key = os.getenv("LEMONSQUEEZY_API_KEY")
    if not api_key:
        raise ValueError(
            "Lemon Squeezy API key not configured. "
            "Set LEMONSQUEEZY_API_KEY in .env"
        )
    return api_key


def _get_store_id() -> str:
    """Get Lemon Squeezy Store ID from environment."""
    store_id = os.getenv("LEMONSQUEEZY_STORE_ID")
    if not store_id:
        raise ValueError(
            "Lemon Squeezy Store ID not configured. "
            "Set LEMONSQUEEZY_STORE_ID in .env"
        )
    return store_id


def _get_headers() -> dict:
    """Get standard headers for Lemon Squeezy API requests."""
    return {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Authorization": f"Bearer {_get_api_key()}",
    }


def create_checkout(
    variant_id: str,
    custom_data: dict,
    user_email: str = "",
    user_name: str = "",
) -> dict:
    """
    Create a Lemon Squeezy checkout session (subscription).

    Args:
        variant_id: The LS variant ID for the product/plan
        custom_data: Dict with user_id, package_id etc. for webhook tracking
        user_email: Pre-fill customer email
        user_name: Pre-fill customer name

    Returns:
        dict with 'checkout_url' and 'checkout_id'
    """
    store_id = _get_store_id()

    checkout_data = {
        "custom": custom_data,
    }
    if user_email:
        checkout_data["email"] = user_email
    if user_name:
        checkout_data["name"] = user_name

    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": checkout_data,
                "checkout_options": {
                    "embed": True,
                    "media": False,
                    "desc": False,
                },
                "product_options": {
                    "enabled_variants": [int(variant_id)],
                    "redirect_url": os.getenv(
                        "FRONTEND_URL",
                        "https://ieltscomputertest.com"
                    ) + "/my-vip-package",
                    "receipt_button_text": "Go to My VIP Package",
                    "receipt_link_url": os.getenv(
                        "FRONTEND_URL",
                        "https://ieltscomputertest.com"
                    ) + "/my-vip-package",
                },
            },
            "relationships": {
                "store": {
                    "data": {
                        "type": "stores",
                        "id": str(store_id),
                    }
                },
                "variant": {
                    "data": {
                        "type": "variants",
                        "id": str(variant_id),
                    }
                },
            },
        }
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{LS_API_BASE}/v1/checkouts",
            json=payload,
            headers=_get_headers(),
        )
        response.raise_for_status()
        result = response.json()

    checkout_url = result["data"]["attributes"]["url"]
    checkout_id = result["data"]["id"]

    logger.info(
        f"Lemon Squeezy checkout created: {checkout_id}, "
        f"variant={variant_id}, custom_data={custom_data}"
    )

    return {
        "checkout_url": checkout_url,
        "checkout_id": checkout_id,
    }


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """
    Verify Lemon Squeezy webhook signature using HMAC-SHA256.

    Args:
        raw_body: The raw request body bytes
        signature: The X-Signature header value

    Returns:
        True if signature is valid
    """
    secret = os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("LEMONSQUEEZY_WEBHOOK_SECRET not set, skipping verification")
        return True  # Skip verification if not configured (dev only)

    expected = hmac.new(
        secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    is_valid = hmac.compare_digest(expected, signature)
    if not is_valid:
        logger.warning("Lemon Squeezy webhook signature verification failed")

    return is_valid


def cancel_subscription(ls_subscription_id: str) -> dict:
    """
    Cancel a Lemon Squeezy subscription.
    The subscription will remain active until the end of the current billing period.

    Args:
        ls_subscription_id: The Lemon Squeezy subscription ID

    Returns:
        dict with subscription status
    """
    with httpx.Client(timeout=30.0) as client:
        response = client.delete(
            f"{LS_API_BASE}/v1/subscriptions/{ls_subscription_id}",
            headers=_get_headers(),
        )
        response.raise_for_status()
        result = response.json()

    status = result["data"]["attributes"]["status"]
    ends_at = result["data"]["attributes"].get("ends_at")

    logger.info(
        f"Lemon Squeezy subscription cancelled: {ls_subscription_id}, "
        f"status={status}, ends_at={ends_at}"
    )

    return {
        "subscription_id": ls_subscription_id,
        "status": status,
        "ends_at": ends_at,
    }


def resume_subscription(ls_subscription_id: str) -> dict:
    """
    Resume a cancelled Lemon Squeezy subscription (before it expires).
    Re-enables auto-renewal so the subscription continues at end of billing period.

    Args:
        ls_subscription_id: The Lemon Squeezy subscription ID

    Returns:
        dict with subscription status
    """
    payload = {
        "data": {
            "type": "subscriptions",
            "id": str(ls_subscription_id),
            "attributes": {
                "cancelled": False,
            },
        }
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.patch(
            f"{LS_API_BASE}/v1/subscriptions/{ls_subscription_id}",
            json=payload,
            headers=_get_headers(),
        )
        response.raise_for_status()
        result = response.json()

    status = result["data"]["attributes"]["status"]
    renews_at = result["data"]["attributes"].get("renews_at")

    logger.info(
        f"Lemon Squeezy subscription resumed: {ls_subscription_id}, "
        f"status={status}, renews_at={renews_at}"
    )

    return {
        "subscription_id": ls_subscription_id,
        "status": status,
        "renews_at": renews_at,
    }


def get_subscription(ls_subscription_id: str) -> dict:
    """
    Get Lemon Squeezy subscription details including management URLs.

    Args:
        ls_subscription_id: The Lemon Squeezy subscription ID

    Returns:
        dict with subscription details and management URLs
    """
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            f"{LS_API_BASE}/v1/subscriptions/{ls_subscription_id}",
            headers=_get_headers(),
        )
        response.raise_for_status()
        result = response.json()

    attrs = result["data"]["attributes"]
    urls = attrs.get("urls", {})

    return {
        "subscription_id": ls_subscription_id,
        "status": attrs.get("status"),
        "renews_at": attrs.get("renews_at"),
        "ends_at": attrs.get("ends_at"),
        "customer_portal_url": urls.get("customer_portal"),
        "update_payment_method_url": urls.get("update_payment_method"),
    }
