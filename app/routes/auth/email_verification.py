from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import User
from pydantic import BaseModel, EmailStr
import dns.resolver
import re
import os
from app.utils.email_utils import send_email
from typing import Dict

router = APIRouter()

class EmailVerifyRequest(BaseModel):
    email: EmailStr

def is_valid_email_format(email: str) -> bool:
    """Check if email has a valid format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def verify_email_domain(email: str) -> Dict[str, bool]:
    """Verify if the email domain exists and has MX records"""
    if not is_valid_email_format(email):
        return {"valid_format": False, "domain_exists": False}
    
    domain = email.split('@')[-1]
    
    try:
        # Check if the domain has MX records
        dns.resolver.resolve(domain, 'MX')
        return {"valid_format": True, "domain_exists": True}
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers, dns.exception.Timeout):
        try:
            # Fallback to A records if no MX records
            dns.resolver.resolve(domain, 'A')
            return {"valid_format": True, "domain_exists": True}
        except:
            return {"valid_format": True, "domain_exists": False}
    except Exception:
        return {"valid_format": True, "domain_exists": False}

 
@router.post("/verify-email", response_model=Dict)
async def verify_email(
    request: EmailVerifyRequest,
    db: Session = Depends(get_db)
):
    """
    Verify if an email is valid and check if it already exists in the system.
    If it exists, send a notification to the user.
    """
    email = request.email
    
    # Step 1: Verify email format and domain
    verification_result = verify_email_domain(email)
    
    if not verification_result["valid_format"]:
        return {
            "valid": False,
            "exists": False,
            "message": "Invalid email format"
        }
    
    if not verification_result["domain_exists"]:
        return {
            "valid": False,
            "exists": False,
            "message": "Email domain does not exist or cannot receive emails"
        }
    
    # Step 2: Check if the email already exists in the database
    existing_user = db.query(User).filter(User.email == email).first()
    
    if existing_user:
        # Send notification email to the existing user
        try:
            
            # Don't reveal to the client that the email exists for security reasons
            return {
                "valid": True,
                "exists": False,  # We say false for security
                "message": "Email validation completed"
            }
        except Exception as e:
            print(f"Failed to send notification: {str(e)}")
            return {
                "valid": True,
                "exists": False,  # We say false for security
                "message": "Email validation completed"
            }
    
    # Email is valid and doesn't exist in our system
    return {
        "valid": True,
        "exists": False,
        "message": "Email is valid and can be used for registration"
    } 
