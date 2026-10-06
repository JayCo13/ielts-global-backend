"""
Email service utility for sending marketing/broadcast emails via AWS SES.

Uses a dedicated SES sender so bulk broadcasts cannot consume the daily
quota of the transactional account used by password reset / verification
(see app/utils/email_utils.py).

Required env vars:
  SES_REGION            e.g. ap-southeast-1
  SES_FROM_EMAIL        e.g. news@ieltscomputertest.com  (must be verified in SES)
  AWS_ACCESS_KEY_ID     IAM user with ses:SendEmail permission
  AWS_SECRET_ACCESS_KEY

The display name in the From header defaults to "IELTS Computer Test" (SES_FROM_NAME).
"""
import os
import logging

logger = logging.getLogger(__name__)

SES_REGION = os.getenv("SES_REGION", "ap-southeast-1")
SES_FROM_EMAIL = os.getenv("SES_FROM_EMAIL", "")
SES_FROM_NAME = os.getenv("SES_FROM_NAME", "IELTS Computer Test")


def _get_ses_client():
    # Imported lazily so the backend can boot without boto3/AWS creds present.
    import boto3
    return boto3.client("ses", region_name=SES_REGION)


def send_single_email(to_email: str, subject: str, body_html: str) -> bool:
    """Send a single marketing email. Prefers Resend when configured, else SES.

    Returns True on success, False on failure.
    """
    # Use Resend for marketing ONLY when explicitly opted in (RESEND_MARKETING),
    # since bulk blasts on Resend can cost far more than SES / a bulk ESP.
    from app.utils.resend_client import resend_marketing_enabled, send_via_resend, marketing_from
    if resend_marketing_enabled():
        return send_via_resend(marketing_from(), to_email, subject, body_html)

    if not SES_FROM_EMAIL:
        logger.error("SES_FROM_EMAIL is not configured")
        return False
    try:
        client = _get_ses_client()
        client.send_email(
            Source=f"{SES_FROM_NAME} <{SES_FROM_EMAIL}>",
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": body_html, "Charset": "UTF-8"}},
            },
        )
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False
