"""
PayOS Payment Gateway Service Layer
Handles all interactions with PayOS API including payment link creation and webhook verification.
"""
import os
import logging
from payos import PayOS
from payos.types import CreatePaymentLinkRequest

logger = logging.getLogger(__name__)

# Initialize PayOS client (singleton)
_payos_client = None


def get_payos_client() -> PayOS:
    """Get or create the PayOS client singleton."""
    global _payos_client
    if _payos_client is None:
        client_id = os.getenv("PAYOS_CLIENT_ID")
        api_key = os.getenv("PAYOS_API_KEY")
        checksum_key = os.getenv("PAYOS_CHECKSUM_KEY")

        if not all([client_id, api_key, checksum_key]):
            raise ValueError(
                "PayOS credentials not configured. "
                "Set PAYOS_CLIENT_ID, PAYOS_API_KEY, PAYOS_CHECKSUM_KEY in .env"
            )

        _payos_client = PayOS(
            client_id=client_id,
            api_key=api_key,
            checksum_key=checksum_key,
        )
        logger.info("PayOS client initialized successfully")

    return _payos_client


def create_payment_link(
    order_code: int,
    amount: int,
    description: str,
    return_url: str,
    cancel_url: str,
) -> dict:
    """
    Create a PayOS payment link for a VIP package purchase.
    
    Args:
        order_code: Unique integer order identifier
        amount: Payment amount in VND (integer, no decimals)
        description: Short description (max 25 chars for PayOS)
        return_url: URL to redirect after successful payment
        cancel_url: URL to redirect after cancelled payment
    
    Returns:
        dict with checkoutUrl, qrCode, etc.
    """
    client = get_payos_client()

    # PayOS description limit is 25 characters
    safe_description = description[:25] if len(description) > 25 else description

    payment_data = CreatePaymentLinkRequest(
        order_code=order_code,
        amount=amount,
        description=safe_description,
        cancel_url=cancel_url,
        return_url=return_url,
    )

    response = client.payment_requests.create(payment_data=payment_data)
    logger.info(f"PayOS payment link created for order_code={order_code}, amount={amount}")
    return response


def verify_webhook(raw_body: bytes) -> dict:
    """
    Verify and parse PayOS webhook data using HMAC_SHA256 checksum.
    
    Args:
        raw_body: Raw request body bytes from the webhook POST
    
    Returns:
        Verified webhook data dict
    
    Raises:
        payos.WebhookError: If checksum verification fails (tampered data)
    """
    client = get_payos_client()
    webhook_data = client.webhooks.verify(raw_body)
    logger.info(f"PayOS webhook verified successfully")
    return webhook_data
