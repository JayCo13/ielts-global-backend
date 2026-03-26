from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, Form, File, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import ExamSection, VIPSubscription, VIPPackage, ExamAccessType, User, UserSession, DeviceViolation, LoginCooldown
from sqlalchemy import or_, and_
from datetime import datetime, timedelta
import pytz
import random
import os
import secrets
import requests
from uuid import uuid4
import string
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from typing import Optional, List
from app.utils.datetime_utils import get_vietnam_time
from datetime import timedelta
from urllib.parse import urlencode, quote_plus
import hashlib
import platform

# Models
class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: int
    username: str
    email: str
    role: str

class StudentCreate(BaseModel):
    username: str
    email: EmailStr
    password: str

class UpdateAdminProfileRequest(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    
class UpdateStudentRequest(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    is_active: Optional[bool] = None
    is_active_student: Optional[bool] = None
    
class LogoutResponse(BaseModel):
    message: str
class CreateStudentRequest(BaseModel):
    username: str
    email: str
    image_url: Optional[str] = None

class GoogleLogin(BaseModel):
    email: EmailStr
    username: str
    google_id: str
    profile_picture: Optional[str] = None

# Configuration
router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

UPLOAD_DIR = "static/student_images"
DEFAULT_STUDENT_IMAGE = "static/student_images/default-img.png" 
SECRET_KEY = os.getenv("SECRET_KEY", "latest-secret-key-here-30-Oct")
SITE_NAME = os.getenv("SITE_NAME", "IELTS Practice")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 90

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_INFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# Device Management Functions
def generate_device_id(user_agent: str, ip_address: str, additional_headers: dict = None) -> str:
    """Generate a deterministic device ID with enhanced fingerprinting for similar devices"""
    
    # Base components - REMOVED timestamp and random components to ensure consistency
    components = [
        user_agent,
        ip_address,
    ]
    
    # Add additional headers if available
    if additional_headers:
        # Screen resolution and display info
        if 'x_screen_resolution' in additional_headers:
            components.append(additional_headers['x_screen_resolution'])
        if 'x_color_depth' in additional_headers:
            components.append(additional_headers['x_color_depth'])
        if 'x_timezone' in additional_headers:
            components.append(additional_headers['x_timezone'])
        if 'accept_language' in additional_headers:
            components.append(additional_headers['accept_language'])
        if 'x_platform' in additional_headers:
            components.append(additional_headers['x_platform'])
        if 'x_hardware_concurrency' in additional_headers:
            components.append(additional_headers['x_hardware_concurrency'])
        if 'x_device_memory' in additional_headers:
            components.append(additional_headers['x_device_memory'])
        
        # Browser-specific fingerprinting
        if 'accept_encoding' in additional_headers:
            components.append(additional_headers['accept_encoding'])
        if 'sec_ch_ua' in additional_headers:
            components.append(additional_headers['sec_ch_ua'])
        if 'sec_ch_ua_platform' in additional_headers:
            components.append(additional_headers['sec_ch_ua_platform'])
        
        # Custom device identifier if provided by frontend (most important)
        if 'x_device_fingerprint' in additional_headers:
            components.append(additional_headers['x_device_fingerprint'])
    
    # Create device string - filter out empty components
    device_string = "|".join(str(c) for c in components if c and str(c).strip())
    
    # Generate SHA256 hash for better uniqueness
    device_id = hashlib.sha256(device_string.encode()).hexdigest()
    
    print(f"DEVICE_ID_GENERATION - Components: {len([c for c in components if c and str(c).strip()])}")
    print(f"DEVICE_ID_GENERATION - Generated ID: {device_id[:16]}...")
    print(f"DEVICE_ID_GENERATION - User Agent: {user_agent[:50]}...")
    print(f"DEVICE_ID_GENERATION - IP: {ip_address}")
    print(f"DEVICE_ID_GENERATION - Device String: {device_string[:100]}...")
    
    return device_id

def generate_unique_session_id() -> str:
    """Generate a unique session ID for each login attempt"""
    import time
    import random
    timestamp = str(int(time.time() * 1000))  # milliseconds
    random_part = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    unique_id = f"{timestamp}_{random_part}"
    return hashlib.sha256(unique_id.encode()).hexdigest()

def create_user_session(db: Session, user_id: int, device_id: str, device_info: str, ip_address: str, session_token: str, unique_session_id: str = None) -> UserSession:
    """Create a new user session"""
    if unique_session_id is None:
        unique_session_id = generate_unique_session_id()
    
    print(f"CREATE_SESSION - Creating session for user {user_id}")
    print(f"CREATE_SESSION - Device ID: {device_id}")
    print(f"CREATE_SESSION - Unique Session ID: {unique_session_id}")
    print(f"CREATE_SESSION - Device Info: {device_info}")
    print(f"CREATE_SESSION - IP Address: {ip_address}")
    
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    current_time = datetime.now(vietnam_tz)
    
    session = UserSession(
        user_id=user_id,
        device_id=device_id,
        unique_session_id=unique_session_id,
        device_info={"user_agent": device_info} if device_info else None,
        ip_address=ip_address,
        login_time=current_time,
        last_activity=current_time,
        is_active=True,
        session_token=session_token
    )
    
    print(f"CREATE_SESSION - Session object created, adding to database")
    db.add(session)
    db.commit()
    db.refresh(session)
    print(f"CREATE_SESSION - Session saved with ID: {session.session_id}")
    return session

# Enhanced Session Management Functions
def record_device_violation(db: Session, user_id: int, device_id: str, violation_type: str = "account_sharing") -> DeviceViolation:
    """Record a user violation for account sharing (user-wide tracking) - No permanent banning, only 10-second cooldowns"""
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    current_time = datetime.now(vietnam_tz)
    
    # Check if there's an existing violation record for this user (any device)
    existing_violation = db.query(DeviceViolation).filter(
        DeviceViolation.user_id == user_id,
        DeviceViolation.violation_type == violation_type
    ).first()
    
    if existing_violation:
        # Update existing violation
        existing_violation.violation_count += 1
        existing_violation.last_violation = current_time
        existing_violation.device_id = device_id  # Update to current device
        # No permanent banning - only temporary 10-second cooldowns
        existing_violation.is_device_banned = False
        db.commit()
        db.refresh(existing_violation)
        violation = existing_violation
    else:
        # Create new violation record
        violation = DeviceViolation(
            user_id=user_id,
            device_id=device_id,
            violation_type=violation_type,
            violation_count=1,
            first_violation=current_time,
            last_violation=current_time,
            is_device_banned=False  # Never permanently ban
        )
        db.add(violation)
        db.commit()
        db.refresh(violation)
    
    print(f"VIOLATION - Recorded {violation_type} violation for user {user_id} (count: {violation.violation_count}, current device: {device_id}) - No permanent ban, only 10s cooldown")
    return violation

def get_device_violation_count(db: Session, user_id: int, device_id: str) -> int:
    """Get the number of violations for a user (user-wide count)"""
    violation_record = db.query(DeviceViolation).filter(
        DeviceViolation.user_id == user_id
    ).first()
    
    count = violation_record.violation_count if violation_record else 0
    print(f"VIOLATION_COUNT - User {user_id} has {count} violations (current device: {device_id})")
    return count

def is_device_banned(db: Session, user_id: int, device_id: str) -> bool:
    """Check if a user is permanently banned - Always returns False (no permanent banning, only 10-second cooldowns)"""
    violation_count = get_device_violation_count(db, user_id, device_id)
    is_banned = False  # Never permanently ban users
    
    print(f"USER_BAN_CHECK - User {user_id}: ALLOWED (no permanent banning - violations: {violation_count}, current device: {device_id})")
    return is_banned

def set_login_cooldown(db: Session, user_id: int, device_id: str, cooldown_seconds: int = 10) -> LoginCooldown:
    """Set a login cooldown for a user (applies to all devices) - Default 10 seconds"""
    from app.utils.datetime_utils import get_vietnam_time
    current_time = get_vietnam_time().replace(tzinfo=None)
    cooldown_end = current_time + timedelta(seconds=cooldown_seconds)
    
    # Remove any existing cooldowns for this user (all devices)
    db.query(LoginCooldown).filter(
        LoginCooldown.user_id == user_id
    ).delete()
    
    # Create a user-wide cooldown (device_id can be used for tracking but cooldown applies to all devices)
    cooldown = LoginCooldown(
        user_id=user_id,
        device_id=device_id,  # Keep for tracking which device triggered the cooldown
        cooldown_end=cooldown_end
    )
    
    db.add(cooldown)
    db.commit()
    db.refresh(cooldown)
    
    print(f"COOLDOWN - Set {cooldown_seconds}-second user-wide cooldown for user {user_id} (triggered by device {device_id}) until {cooldown_end}")
    return cooldown

def is_device_in_cooldown(db: Session, user_id: int, device_id: str) -> bool:
    """Check if a user is currently in cooldown period (applies to all devices)"""
    from app.utils.datetime_utils import get_vietnam_time
    current_time = get_vietnam_time().replace(tzinfo=None)
    
    # Check for any active cooldown for this user (regardless of device)
    cooldown = db.query(LoginCooldown).filter(
        LoginCooldown.user_id == user_id,
        LoginCooldown.cooldown_end > current_time
    ).first()
    
    is_in_cooldown = cooldown is not None
    if is_in_cooldown:
        print(f"COOLDOWN_CHECK - User {user_id} is in cooldown until {cooldown.cooldown_end} (triggered by device {cooldown.device_id}, current device: {device_id})")
    else:
        print(f"COOLDOWN_CHECK - User {user_id} is not in cooldown (current device: {device_id})")
    
    return is_in_cooldown

def get_cooldown_remaining_time(db: Session, user_id: int, device_id: str) -> Optional[int]:
    """Get remaining cooldown time in seconds for a user, None if no cooldown"""
    from app.utils.datetime_utils import get_vietnam_time
    current_time = get_vietnam_time().replace(tzinfo=None)
    
    # Check for any active cooldown for this user (regardless of device)
    cooldown = db.query(LoginCooldown).filter(
        LoginCooldown.user_id == user_id,
        LoginCooldown.cooldown_end > current_time
    ).first()
    
    if cooldown:
        remaining_seconds = int((cooldown.cooldown_end - current_time).total_seconds())
        print(f"COOLDOWN_REMAINING - User {user_id} has {remaining_seconds} seconds remaining (triggered by device {cooldown.device_id}, current device: {device_id})")
        return remaining_seconds
    
    return None

def cleanup_expired_cooldowns(db: Session) -> None:
    """Clean up expired user cooldown records"""
    from app.utils.datetime_utils import get_vietnam_time
    current_time = get_vietnam_time().replace(tzinfo=None)
    
    deleted_count = db.query(LoginCooldown).filter(
        LoginCooldown.cooldown_end <= current_time
    ).delete()
    
    if deleted_count > 0:
        db.commit()
        print(f"CLEANUP - Removed {deleted_count} expired user cooldown records")

def get_active_sessions(db: Session, user_id: int) -> List[UserSession]:
    """Get all active sessions for a user, automatically expiring old sessions"""
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    current_time = datetime.now(vietnam_tz)
    
    # Define session expiration time (24 hours of inactivity)
    session_expiry_hours = 24
    expiry_threshold = current_time - timedelta(hours=session_expiry_hours)
    
    # First, expire old sessions that haven't been active for more than 24 hours
    expired_sessions = db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.is_active == True,
        UserSession.last_activity < expiry_threshold.replace(tzinfo=None)
    ).all()
    
    if expired_sessions:
        print(f"SESSION_EXPIRY - Found {len(expired_sessions)} expired sessions for user {user_id}")
        for session in expired_sessions:
            print(f"SESSION_EXPIRY - Expiring session {session.session_id}, last activity: {session.last_activity}")
        
        # Mark expired sessions as inactive
        db.query(UserSession).filter(
            UserSession.user_id == user_id,
            UserSession.is_active == True,
            UserSession.last_activity < expiry_threshold.replace(tzinfo=None)
        ).update({
            'is_active': False,
            'logout_time': current_time.replace(tzinfo=None)
        })
        db.commit()
    
    # Return only truly active sessions (not expired)
    active_sessions = db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.is_active == True
    ).all()
    
    print(f"SESSION_CHECK - User {user_id} has {len(active_sessions)} active sessions after expiry cleanup")
    return active_sessions

def logout_all_sessions(db: Session, user_id: int) -> None:
    """Logout all active sessions for a user"""
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    current_time = datetime.now(vietnam_tz)
    
    db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.is_active == True
    ).update({
        'is_active': False,
        'logout_time': current_time
    })
    db.commit()

def update_session_activity(db: Session, session_token: str) -> None:
    """Update last activity time for a session"""
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    current_time = datetime.now(vietnam_tz)
    
    db.query(UserSession).filter(
        UserSession.session_token == session_token,
        UserSession.is_active == True
    ).update({'last_activity': current_time})
    db.commit()

def get_current_session(db: Session, session_token: str) -> UserSession:
    """Get current session by token"""
    return db.query(UserSession).filter(
        UserSession.session_token == session_token,
        UserSession.is_active == True
    ).first()

def check_behavioral_patterns(db: Session, user_id: int, device_id: str) -> bool:
    """
    Check for suspicious behavioral patterns that might indicate account sharing
    """
    try:
        # Get recent sessions (last 10 minutes)
        recent_time = get_vietnam_time() - timedelta(minutes=10)
        recent_sessions = db.query(UserSession).filter(
            UserSession.user_id == user_id,
            UserSession.created_at >= recent_time,
            UserSession.is_active == True
        ).all()
        
        if len(recent_sessions) < 2:
            return False
            
        # Check for rapid logins from different devices
        device_login_times = {}
        for session in recent_sessions:
            session_device_id = session.device_id
            if session_device_id not in device_login_times:
                device_login_times[session_device_id] = []
            device_login_times[session_device_id].append(session.created_at)
        
        # If multiple devices logged in within 2 minutes, it's suspicious
        if len(device_login_times) > 1:
            login_times = []
            for device_times in device_login_times.values():
                login_times.extend(device_times)
            
            login_times.sort()
            
            # Check if any two logins from different devices happened within 2 minutes
            for i in range(len(login_times) - 1):
                time_diff = (login_times[i + 1] - login_times[i]).total_seconds()
                if time_diff < 120:  # 2 minutes
                    print(f"BEHAVIORAL ALERT - Rapid logins detected for user {user_id}: {time_diff} seconds apart")
                    return True
        
        # Check for simultaneous activity patterns
        active_devices = set()
        for session in recent_sessions:
            if session.last_activity and session.last_activity >= recent_time:
                active_devices.add(session.device_id)
        
        if len(active_devices) > 1:
            print(f"BEHAVIORAL ALERT - Multiple devices active simultaneously for user {user_id}: {len(active_devices)} devices")
            return True
            
        return False
        
    except Exception as e:
        print(f"Error in behavioral pattern check: {e}")
        return False

def check_multiple_sessions(db: Session, user_id: int, current_session_token: str = None) -> bool:
    """Check if user has multiple active sessions from different devices (detects account sharing)"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"🔍 SESSION CHECK - User ID: {user_id}")
    logger.info(f"🔍 CURRENT SESSION TOKEN: {current_session_token[:20] + '...' if current_session_token else 'None'}")
    
    active_sessions = get_active_sessions(db, user_id)
    logger.info(f"📱 TOTAL ACTIVE SESSIONS COUNT: {len(active_sessions)}")
    
    # If current_session_token is provided, filter out the current session
    if current_session_token:
        other_sessions = [s for s in active_sessions if s.session_token != current_session_token]
        logger.info(f"📱 OTHER SESSIONS COUNT (excluding current): {len(other_sessions)}")
        
        for i, session in enumerate(other_sessions):
            token_preview = session.session_token[:10] + "..." if session.session_token else "None"
            unique_session_preview = session.unique_session_id[:10] + "..." if session.unique_session_id else "None"
            logger.info(f"📱 Other Session {i+1}: Device ID: {session.device_id}, Unique Session: {unique_session_preview}, Token: {token_preview}, Last Activity: {session.last_activity}")
        
        # Get current session to compare device IDs
        current_session = get_current_session(db, current_session_token)
        current_device_id = current_session.device_id if current_session else None
        logger.info(f"📱 CURRENT DEVICE ID: {current_device_id}")
        
        # Account sharing detected only if there are sessions from different device IDs
        different_device_sessions = [s for s in other_sessions if s.device_id != current_device_id]
        has_multiple_devices = len(different_device_sessions) > 0
        
        logger.info(f"📱 SESSIONS FROM DIFFERENT DEVICES: {len(different_device_sessions)}")
        for i, session in enumerate(different_device_sessions):
            logger.info(f"📱 Different Device Session {i+1}: Device ID: {session.device_id}")
        
        has_multiple = has_multiple_devices
    else:
        # Fallback: check for sessions from different device IDs
        for i, session in enumerate(active_sessions):
            token_preview = session.session_token[:10] + "..." if session.session_token else "None"
            unique_session_preview = session.unique_session_id[:10] + "..." if session.unique_session_id else "None"
            logger.info(f"📱 Session {i+1}: Device ID: {session.device_id}, Unique Session: {unique_session_preview}, Token: {token_preview}, Last Activity: {session.last_activity}")
        
        # Get unique device IDs from all active sessions
        unique_device_ids = set(session.device_id for session in active_sessions if session.device_id)
        has_multiple = len(unique_device_ids) > 1
        
        logger.info(f"📱 UNIQUE DEVICE IDS: {list(unique_device_ids)}")
    
    logger.info(f"🚨 MULTIPLE DEVICES DETECTED: {has_multiple}")
    
    return has_multiple

def check_multiple_devices(db: Session, user_id: int, current_device_id: str) -> bool:
    """Legacy function - now redirects to session-based checking"""
    return check_multiple_sessions(db, user_id)

def validate_session_integrity(db: Session, session_token: str) -> bool:
    """Validate if the current session is still the only active session for the user"""
    import logging
    logger = logging.getLogger(__name__)
    
    # Get current session
    current_session = get_current_session(db, session_token)
    if not current_session:
        logger.warning(f"🚨 SESSION VALIDATION - Session not found for token")
        return False
    
    # Check if there are other active sessions for this user
    active_sessions = get_active_sessions(db, current_session.user_id)
    
    # Filter out the current session
    other_sessions = [s for s in active_sessions if s.session_token != session_token]
    
    if other_sessions:
        logger.warning(f"🚨 SESSION VALIDATION - Found {len(other_sessions)} other active sessions for user {current_session.user_id}")
        for i, session in enumerate(other_sessions):
            unique_session_preview = session.unique_session_id[:10] + "..." if session.unique_session_id else "None"
            logger.warning(f"🚨 Other Session {i+1}: Unique Session: {unique_session_preview}, Last Activity: {session.last_activity}")
        return False
    
    logger.info(f"✅ SESSION VALIDATION - Session is valid for user {current_session.user_id}")
    return True

# Utility functions
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    
    return user
async def get_current_user_ws(token: str, db: Session) -> User:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user
def create_or_update_student(
    db: Session,
    email: str,
    username: str,
    google_id: str = None,
    profile_picture: str = None,
    password: str = None
) -> User:
    student = db.query(User).filter(User.email == email).first()
    
    if student:
        # Update existing student
        if google_id and not student.google_id:
            student.google_id = google_id
        if profile_picture and not student.image_url:
            student.image_url = profile_picture
        student.status = "online"
        student.last_active = get_vietnam_time().replace(tzinfo=None)
    else:
        # Create new student
        # Check if username already exists and modify it if needed
        original_username = username
        suffix = 1
        
        while db.query(User).filter(User.username == username).first():
            # Username exists, append a random suffix
            random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
            username = f"{original_username}_{random_suffix}"
            
        hashed_password = get_password_hash(password or secrets.token_hex(16))
        student = User(
            username=username,
            email=email,
            password=hashed_password,
            google_id=google_id,
            image_url=profile_picture or DEFAULT_STUDENT_IMAGE,
            role="customer",
            status="online",
            created_at=get_vietnam_time().replace(tzinfo=None),
            last_active=get_vietnam_time().replace(tzinfo=None)
        )
        db.add(student)
    
    db.commit()
    db.refresh(student)
    return student
 

async def check_exam_access(user: User, exam_id: int, db: Session) -> bool:
    """Check if a user has access to a specific exam based on their role and VIP status"""
    
    # Get exam section type
    exam_section = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id
    ).first()
    
    if not exam_section:
        return False
    
    # Speaking is free for all
    if exam_section.section_type == 'speaking':
        return True
    
    # For customers, check VIP status and test limits
    if user.role == "customer":
        # Check test limit for non-VIP users
        if not user.is_vip:
            # Remove the recursive call that's causing the error
            # For non-VIP users, we'll just check access types below
            pass
        else:
            # Check if user has VIP access for this skill
            active_subscription = db.query(VIPSubscription).join(VIPPackage).filter(
                VIPSubscription.user_id == user.user_id,
                VIPSubscription.end_date > get_vietnam_time().replace(tzinfo=None),
                VIPSubscription.payment_status == "completed",
                or_(
                    VIPPackage.package_type == 'all_skills',
                    and_(
                        VIPPackage.package_type == 'single_skill',
                        VIPPackage.skill_type == exam_section.section_type
                    )
                )
            ).first()
            
            if not active_subscription:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"You need a VIP subscription for {exam_section.section_type} tests"
                )
    
    # Get exam access types
    exam_access_types = db.query(ExamAccessType)\
        .filter(ExamAccessType.exam_id == exam_id)\
        .all()
    
    allowed_types = []
    
    if user.role == 'student':
        allowed_types = ['student']
    elif user.role == 'customer':
        if user.is_vip:
            allowed_types = ['no vip', 'vip']
        else:
            allowed_types = ['no vip']
    elif user.role == 'admin':
        allowed_types = ['student', 'no vip', 'vip']
    
    # Check if any of the exam's access types match the user's allowed types
    exam_types = [access.access_type for access in exam_access_types]
    return any(access_type in allowed_types for access_type in exam_types)
def generate_random_password(length=6):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

# Replace datetime.utcnow() with get_vietnam_time() in functions like:
def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = get_vietnam_time() + expires_delta
    else:
        expire = get_vietnam_time() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_student(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    user = db.query(User).filter(User.username == username).first()
    if user is None or user.role not in ["student", "customer"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students and customers can perform this action"
        )
    
    # Check if account has expired (only for student accounts) - do this BEFORE is_active check
    if user.role == "student" and user.account_activated_at is not None:
        expiry_date = user.account_activated_at + timedelta(days=90)
        if get_vietnam_time().replace(tzinfo=None) > expiry_date:
            # Convert student to customer and reactivate
            user.role = "customer"
            user.is_active = True  # Reactivate the account
            db.commit()
            # User continues as customer with VIP restrictions
    
    # Check if account is active (skip for converted customers)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active"
        )
    
    return user

async def get_current_admin(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    user = db.query(User).filter(User.username == username).first()
    if user is None or user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can perform this action"
        )
    return user

@router.post("/admin-tajun/login")
async def admin_login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == form_data.username).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username does not exist",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if user is an admin
    if user.role != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Admin privileges required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not pwd_context.verify(form_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "username": user.username,
        "user_id": user.user_id,
        "email": user.email,
        "is_active": user.is_active
    }
@router.post("/logout", response_model=LogoutResponse)
async def logout(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Logout endpoint that updates user status to offline and sets last_active to current Vietnam time"""
    # Update the user's status to offline
    current_user.status = "offline"
    
    # Update last_active with Vietnam time
    vietnam_time = get_vietnam_time()
    current_user.last_active = vietnam_time.replace(tzinfo=None)  # Remove timezone info for DB storage
    
    # If using UserSession table, update logout_time for ALL active sessions
    active_sessions_count = db.query(UserSession).filter(
        UserSession.user_id == current_user.user_id,
        UserSession.is_active == True
    ).update({
        'logout_time': vietnam_time.replace(tzinfo=None),
        'is_active': False
    })
    
    print(f"LOGOUT - Deactivated {active_sessions_count} sessions for user {current_user.user_id} ({current_user.username})")
    
    db.commit()
    
    return {"message": "Successfully logged out"}
@router.get("/google-auth")
async def google_auth(request: Request):
    """Initiates the Google OAuth flow"""
    callback_uri = GOOGLE_REDIRECT_URI or f"{request.base_url.scheme}://{request.base_url.netloc}/google-callback"
    
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": callback_uri,  # Use dynamic callback URI
        "access_type": "offline",
        "prompt": "consent select_account"
    }
    
    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params, quote_via=quote_plus)}"
    return RedirectResponse(auth_url)

@router.get("/google-callback")
async def google_callback(
    code: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """Handles the Google OAuth callback"""
    try:
        callback_uri = GOOGLE_REDIRECT_URI or f"{request.base_url.scheme}://{request.base_url.netloc}/google-callback"
        
        # Exchange code for token
        token_response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": callback_uri,  # Use dynamic callback URI
                "grant_type": "authorization_code",
            },
            timeout=10
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        
        # Get user info
        user_response = requests.get(
            GOOGLE_USER_INFO_URL,
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
            timeout=10
        )
        user_response.raise_for_status()
        user_info = user_response.json()
        
        # Extract user data
        email = user_info.get("email")
        if not email:
            raise HTTPException(status_code=400, detail="Email not provided by Google")
        
        # Create or update student
        student = create_or_update_student(
            db=db,
            email=email,
            username=user_info.get("name", email.split("@")[0]),
            google_id=user_info.get("sub"),
            profile_picture=user_info.get("picture")
        )
        
        # Get device information for tracking (same as regular login)
        user_agent = request.headers.get("user-agent", "")
        ip_address = request.client.host if request.client else ""
        device_id = generate_device_id(user_agent, ip_address)
        
        print(f"GOOGLE LOGIN - User {student.user_id} ({student.username}) logging in")
        print(f"GOOGLE LOGIN - Device ID: {device_id}")
        print(f"GOOGLE LOGIN - User Agent: {user_agent}")
        print(f"GOOGLE LOGIN - IP Address: {ip_address}")
        
        # Clean up expired cooldowns
        cleanup_expired_cooldowns(db)
        
        # Check if device is in cooldown
        if is_device_in_cooldown(db, student.user_id, device_id):
            remaining_time = get_cooldown_remaining_time(db, student.user_id, device_id)
            error_params = {
                "error": f"DEVICE_IN_COOLDOWN:{remaining_time}",
                "message": "Device is in cooldown period"
            }
            error_redirect_url = f"{os.getenv('FRONTEND_URL')}/auth-callback?{urlencode(error_params, quote_via=quote_plus)}"
            return RedirectResponse(error_redirect_url)
        
        # Check for multiple active sessions before creating new session (same logic as normal login)
        if check_multiple_sessions(db, student.user_id):
            print(f"GOOGLE LOGIN - Account sharing detected for user {student.user_id}")
            
            # Record violation for account sharing (for monitoring only, no permanent ban)
            record_device_violation(db, student.user_id, device_id, "account_sharing")
            
            # Logout all existing sessions (this was missing in Google login!)
            logout_all_sessions(db, student.user_id)
            
            # Set 10-second cooldown period for this user
            set_login_cooldown(db, student.user_id, device_id, cooldown_seconds=10)
            
            # Return error response
            error_params = {
                "error": "ACCOUNT_SHARING_DETECTED",
                "message": "Multiple device login detected"
            }
            error_redirect_url = f"{os.getenv('FRONTEND_URL')}/auth-callback?{urlencode(error_params, quote_via=quote_plus)}"
            return RedirectResponse(error_redirect_url)
        
        # Create access token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": student.username},
            expires_delta=access_token_expires
        )
        
        # Generate unique session ID for this login
        unique_session_id = generate_unique_session_id()
        
        print(f"GOOGLE LOGIN - Creating session for user {student.user_id} with device {device_id}")
        print(f"GOOGLE LOGIN - Unique Session ID: {unique_session_id}")
        
        # Create user session with unique session ID
        create_user_session(
            db=db,
            user_id=student.user_id,
            device_id=device_id,
            device_info=user_agent,
            ip_address=ip_address,
            session_token=access_token,
            unique_session_id=unique_session_id
        )
        
        print(f"GOOGLE LOGIN - Session created successfully for user {student.user_id}")

        # Redirect to frontend with session information
        params = {
            "token": access_token,
            "user_id": student.user_id,
            "username": student.username,
            "email": student.email,
            "role": student.role,
            "device_id": device_id,
            "unique_session_id": unique_session_id
        }
        redirect_url = f"{os.getenv('FRONTEND_URL')}/auth-callback?{urlencode(params, quote_via=quote_plus)}"
        return RedirectResponse(redirect_url)
        
    except requests.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to communicate with Google: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
@router.post("/register", response_model=Token)
async def register_student(student_data: StudentCreate, db: Session = Depends(get_db)):
    # Check if email already exists
    existing_user = db.query(User).filter(User.email == student_data.email).first()
    if existing_user:
        # If the email is already registered, send a notification email
        try:
            from app.utils.email_utils import send_email
            
            # Create email content
            subject = f"{SITE_NAME} - Email đã được sử dụng"
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 5px;">
                    <h2 style="color: #4a86e8;">{SITE_NAME} - Tài khoản đã tồn tại</h2>
                    <p>Xin chào,</p>
                    <p>Chúng tôi nhận thấy bạn đã cố gắng đăng ký với địa chỉ email này, nhưng một tài khoản đã tồn tại.</p>
                    <p>Nếu bạn quên mật khẩu, bạn có thể đặt lại bằng cách sử dụng liên kết "Quên mật khẩu" trên trang đăng nhập.</p>
                    <p>Nếu bạn không cố gắng đăng ký, bạn có thể bỏ qua email này.</p>
                    <p>Cảm ơn bạn,<br>Đội ngũ {SITE_NAME}</p>
                </div>
            </body>
            </html>
            """
            
            # Send the email silently (don't raise exceptions if it fails)
            try:
                send_email(student_data.email, subject, html_content)
            except Exception as e:
                print(f"Failed to send 'account exists' email: {str(e)}")
                # Don't block the response, just log the error
                
        except ImportError:
            # If email utils aren't available, just continue
            pass
            
        # Return the same error response
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
        
    # Check if username already exists
    existing_user_name = db.query(User).filter(User.username == student_data.username).first()
    if existing_user_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Validate if the email domain is real
    try:
        from app.utils.email_utils import is_valid_email
        
        if not is_valid_email(student_data.email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid email address. Please provide a valid email."
            )
    except ImportError:
        # If email validation isn't available, continue with registration
        pass
    
    # Create new student
    hashed_password = get_password_hash(student_data.password)
    new_student = User(
        username=student_data.username,
        email=student_data.email,
        password=hashed_password,
        role="customer",   
        status="online",
        created_at=get_vietnam_time().replace(tzinfo=None),
        last_active=get_vietnam_time().replace(tzinfo=None),
        image_url=DEFAULT_STUDENT_IMAGE  # Set default image
    )
    
    db.add(new_student)
    db.commit()
    db.refresh(new_student)
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": new_student.username},  # Change from user_id to username
        expires_delta=access_token_expires
    )
    
    # Send welcome email
    try:
        from app.utils.email_utils import send_account_created_email
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        
        # Send the welcome email silently (don't raise exceptions if it fails)
        try:
            # Customize the welcome email in Vietnamese directly here
            from app.utils.email_utils import send_email
            
            subject = f"{SITE_NAME} - Chào mừng bạn đến với hệ thống"
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 5px;">
                    <h2 style="color: #4a86e8;">Chào mừng đến với {SITE_NAME}!</h2>
                    <p>Xin chào {new_student.username},</p>
                    <p>Chúng tôi rất vui mừng thông báo rằng tài khoản Ielttrenmay của bạn đã được tạo thành công.</p>
                    <p>Bạn có thể đăng nhập ngay bây giờ để truy cập tất cả các tài liệu ôn thi IELTS của chúng tôi.</p>
                    <p>
                        <a href="{frontend_url}/login" style="display: inline-block; padding: 10px 20px; background-color: #4a86e8; color: white; text-decoration: none; border-radius: 5px;">
                            Đăng nhập ngay
                        </a>
                    </p>
                    <p>Nếu bạn có bất kỳ câu hỏi hoặc cần hỗ trợ, đừng ngần ngại liên hệ với đội ngũ hỗ trợ của chúng tôi.</p>
                    <p>Cảm ơn bạn,<br>Đội ngũ {SITE_NAME}</p>
                </div>
            </body>
            </html>
            """
            
            # Send the custom Vietnamese email
            send_email(new_student.email, subject, html_content)
        except Exception as e:
            print(f"Failed to send welcome email: {str(e)}")
            # Don't block the registration if email fails
    except ImportError:
        # If email utils aren't available, just continue
        pass
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": new_student.user_id,
        "username": new_student.username,
        "email": new_student.email,
        "role": new_student.role
    }
@router.post("/login")
async def admin_login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    request: Request = None,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == form_data.username).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username does not exist",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Block specific email address
    if user.email == os.getenv("BLOCKED_EMAIL", ""):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been blocked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not pwd_context.verify(form_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get device information
    user_agent = request.headers.get("user-agent", "") if request else ""
    ip_address = request.client.host if request else ""
    
    # Collect additional headers for enhanced device fingerprinting
    additional_headers = {}
    if request:
        # Standard headers that might vary between devices
        additional_headers.update({
            'accept_language': request.headers.get("accept-language", ""),
            'accept_encoding': request.headers.get("accept-encoding", ""),
            'accept': request.headers.get("accept", ""),
            'sec_ch_ua': request.headers.get("sec-ch-ua", ""),
            'sec_ch_ua_mobile': request.headers.get("sec-ch-ua-mobile", ""),
            'sec_ch_ua_platform': request.headers.get("sec-ch-ua-platform", ""),
            'sec_fetch_dest': request.headers.get("sec-fetch-dest", ""),
            'sec_fetch_mode': request.headers.get("sec-fetch-mode", ""),
            'sec_fetch_site': request.headers.get("sec-fetch-site", ""),
            'upgrade_insecure_requests': request.headers.get("upgrade-insecure-requests", ""),
            'cache_control': request.headers.get("cache-control", ""),
            'pragma': request.headers.get("pragma", ""),
            'connection': request.headers.get("connection", ""),
            'dnt': request.headers.get("dnt", ""),
            'x_forwarded_for': request.headers.get("x-forwarded-for", ""),
            'x_real_ip': request.headers.get("x-real-ip", ""),
            # Custom device fingerprint headers from frontend
            'x_device_fingerprint': request.headers.get("x-device-fingerprint", ""),
            'x_screen_resolution': request.headers.get("x-screen-resolution", ""),
            'x_timezone': request.headers.get("x-timezone", ""),
            'x_platform': request.headers.get("x-platform", ""),
            'x_hardware_concurrency': request.headers.get("x-hardware-concurrency", ""),
            'x_device_memory': request.headers.get("x-device-memory", ""),
            'x_color_depth': request.headers.get("x-color-depth", ""),
        })
    
    device_id = generate_device_id(user_agent, ip_address, additional_headers)
    
    print(f"LOGIN - User {user.user_id} ({user.username}) logging in")
    print(f"LOGIN - Device ID: {device_id}")
    print(f"LOGIN - User Agent: {user_agent}")
    print(f"LOGIN - IP Address: {ip_address}")
    
    # Clean up expired cooldowns
    cleanup_expired_cooldowns(db)
    
    # No permanent banning - only temporary 10-second cooldowns for account sharing
    
    # Check if device is in cooldown period
    if is_device_in_cooldown(db, user.user_id, device_id):
        remaining_time = get_cooldown_remaining_time(db, user.user_id, device_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"DEVICE_IN_COOLDOWN:{remaining_time}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check for multiple active sessions and behavioral patterns before creating new session
    multiple_sessions_detected = check_multiple_sessions(db, user.user_id)
    behavioral_patterns_detected = check_behavioral_patterns(db, user.user_id, device_id)
    
    if multiple_sessions_detected or behavioral_patterns_detected:
        violation_type = "account_sharing"
        if behavioral_patterns_detected:
            violation_type = "suspicious_behavior"
            
        # Record violation for account sharing (for monitoring only, no permanent ban)
        record_device_violation(db, user.user_id, device_id, violation_type)
        
        # Logout all existing sessions
        logout_all_sessions(db, user.user_id)
        
        # Set 10-second cooldown period for this user
        set_login_cooldown(db, user.user_id, device_id, cooldown_seconds=10)
        
        # Return account sharing detected with 10-second cooldown
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ACCOUNT_SHARING_DETECTED",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )
    
    # Generate unique session ID for this login
    unique_session_id = generate_unique_session_id()
    
    print(f"LOGIN - Creating session for user {user.user_id} with device {device_id}")
    print(f"LOGIN - Unique Session ID: {unique_session_id}")
    
    # Create user session with unique session ID
    create_user_session(
        db=db,
        user_id=user.user_id,
        device_id=device_id,
        device_info=user_agent,
        ip_address=ip_address,
        session_token=access_token,
        unique_session_id=unique_session_id
    )
    
    print(f"LOGIN - Session created successfully for user {user.user_id}")

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "username": user.username,
        "user_id": user.user_id,
        "email": user.email,
        "is_active": user.is_active,
        "device_id": device_id,
        "unique_session_id": unique_session_id
    }

@router.post("/check-device")
async def check_device(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Check if user has multiple sessions and handle accordingly"""
    # Get current session token
    token = request.headers.get("authorization", "").replace("Bearer ", "")
    
    # Check for multiple sessions
    if check_multiple_sessions(db, current_user.user_id, token):
        # Logout all existing sessions
        logout_all_sessions(db, current_user.user_id)
        
        # Return special response indicating force logout
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ACCOUNT_SHARING_DETECTED",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Validate session integrity
    validate_session_integrity(db, token)
    
    # Update session activity
    if token:
        update_session_activity(db, token)
    
    # Get device information for response
    user_agent = request.headers.get("user-agent", "")
    ip_address = request.client.host
    device_id = generate_device_id(user_agent, ip_address)
    
    return {
        "status": "success",
        "device_id": device_id,
        "message": "Session check passed"
    }

@router.get("/admin/profile", response_model=dict)
async def get_admin_profile(
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    image_url = f"{request.base_url}{current_admin.image_url}" if current_admin.image_url else None
    
    return {
        "user_id": current_admin.user_id,
        "username": current_admin.username,
        "email": current_admin.email,
        "image_url": image_url,
        "role": current_admin.role
    }
@router.post("/students/{student_id}/reset-password", response_model=dict)
async def reset_student_password(
    student_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    student = db.query(User).filter(
        User.user_id == student_id, 
        User.role.in_(["student", "customer"])
    ).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    new_password = generate_random_password()
    hashed_password = pwd_context.hash(new_password)
    
    student.password = hashed_password
    db.commit()
    
    return {
        "message": "Password reset successfully",
        "username": student.username,
        "new_temporary_password": new_password
    }
@router.put("/admin/profile", response_model=dict)
async def update_admin_profile(
    email: str = Form(None),
    username: str = Form(None),
    image: UploadFile = File(None),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    if email:
        current_admin.email = email
    
    if username:
        current_admin.username = username
    
    if image:
        file_extension = os.path.splitext(image.filename)[1]
        unique_filename = f"{uuid4()}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        
        with open(file_path, "wb") as buffer:
            content = await image.read()
            buffer.write(content)
        
        current_admin.image_url = file_path

    db.commit()
    db.refresh(current_admin)
    
    return {
        "message": "Profile updated successfully",
        "email": current_admin.email,
        "username": current_admin.username,
        "image_url": current_admin.image_url,
        "role": current_admin.role
    }   
    
@router.post("/create-student", response_model=dict)
def create_student(
    student: CreateStudentRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    vietnam_time = datetime.now(vietnam_tz)
    
    raw_password = generate_random_password()
    hashed_password = pwd_context.hash(raw_password)
    
    new_student = User(
        username=student.username,
        email=student.email,
        password=hashed_password,
        role="student",
        is_active=True,
        created_at=vietnam_time,
        image_url=student.image_url or DEFAULT_STUDENT_IMAGE,
        status='offline'
    )
    
    db.add(new_student)
    db.commit()
    db.refresh(new_student)
    
    return {
        "message": "Student account created successfully",
        "username": student.username,
        "temporary_password": raw_password,
        "image_url": new_student.image_url,
        "status": new_student.status
    }
    
@router.get("/students", response_model=List[dict])
async def get_students(
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    students = db.query(User).all()
    
    # Define the threshold for considering a user offline (15 minutes)
    offline_threshold = get_vietnam_time().replace(tzinfo=None) - timedelta(minutes=15)
    
    return [{
        "user_id": student.user_id, 
        "username": student.username, 
        "created_at": student.created_at.astimezone(vietnam_tz), 
        "email": student.email,
        "last_active": student.last_active.astimezone(vietnam_tz) if student.last_active else None,
        "image_url": f"{os.getenv('BASE_URL', 'http://localhost:8000')}/static/student_images/{student.image_url.split('/')[-1]}" if student.image_url else None,
        # Determine status based on last_active timestamp - ensure both times are in UTC for comparison
        "status": "offline" if student.status == "offline" or (student.last_active and student.last_active < offline_threshold) else "online",
        "is_active": student.is_active,
        "is_active_student": getattr(student, 'is_active_student', False)
    } for student in students]
# admin reset student password
@router.post("/students/{student_id}/reset-password", response_model=dict)
async def reset_student_password(
    student_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    student = db.query(User).filter(User.user_id == student_id, User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    new_password = generate_random_password()
    hashed_password = pwd_context.hash(new_password)
    
    student.password = hashed_password
    db.commit()
    
    return {
        "message": "Password reset successfully",
        "username": student.username,
        "new_temporary_password": new_password
    }

@router.get("/students/{student_id}", response_model=dict)
async def get_student(
    student_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    user = db.query(User).filter(
        User.user_id == student_id,
        User.role.in_(["student", "customer"])
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Student not found")
    return {
         "user_id": user.user_id, 
        "username": user.username, 
        "created_at": user.created_at.astimezone(vietnam_tz), 
        "email": user.email,
       "image_url": f"{os.getenv('BASE_URL', 'http://localhost:8000')}/static/student_images/{user.image_url.split('/')[-1]}" if user.image_url else None,
        "status": user.status,
        "is_active": user.is_active,
        "is_active_student": user.is_active_student
    }
@router.get("/students/student-side/{student_id}", response_model=dict)
async def get_student(
    student_id: int,
    current_admin: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    student = db.query(User).filter(User.user_id == student_id, User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return {
         "user_id": student.user_id, 
        "username": student.username, 
        "created_at": student.created_at.astimezone(vietnam_tz), 
        "email": student.email,
       "image_url": f"{os.getenv('BASE_URL', 'http://localhost:8000')}/static/student_images/{student.image_url.split('/')[-1]}" if student.image_url else None,
        "status": student.status,
        "is_active": student.is_active,
        "is_active_student": student.is_active_student
    }
@router.put("/students/{student_id}", response_model=dict)
async def update_student(
    student_id: int,
    student_data: UpdateStudentRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    student = db.query(User).filter(
        User.user_id == student_id,
        User.role.in_(["student", "customer"])
    ).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    if student_data.email:
        student.email = student_data.email
    
    if student_data.username:
        student.username = student_data.username
        
    if student_data.is_active is not None:
        student.is_active = student_data.is_active
        
    if student_data.is_active_student is not None:
        student.is_active_student = student_data.is_active_student
    
    db.commit()
    db.refresh(student)
    
    return {
        "message": "Student updated successfully",
        "user_id": student.user_id,
        "username": student.username,
        "email": student.email,
        "is_active": student.is_active,
        "is_active_student": student.is_active_student
    }

# Add these endpoints to the auth.py file
@router.post("/activate-account", response_model=dict)
async def activate_student_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Activate a student account for a 3-month period.
    This should be called when a student first logs in after account creation.
    """
    # Ensure this is a student account
    if current_user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only student accounts can be activated"
        )
        
    # Check if account is already activated
    if current_user.account_activated_at is not None:
        # Calculate remaining days
        expiry_date = current_user.account_activated_at + timedelta(days=90)
        remaining_days = (expiry_date - get_vietnam_time().replace(tzinfo=None)).days
        
        if remaining_days > 0:
            return {
                "message": "Account already activated",
                "user_id": current_user.user_id,
                "activated_at": current_user.account_activated_at,
                "expires_at": expiry_date,
                "remaining_days": remaining_days
            }
    
    # Activate the account
    current_user.account_activated_at = get_vietnam_time().replace(tzinfo=None)
    current_user.is_active = True
    current_user.is_active_student = True
    db.commit()
    
    # Calculate expiry date
    expiry_date = current_user.account_activated_at + timedelta(days=90)
    
    return {
        "message": "Account activated successfully",
        "user_id": current_user.user_id,
        "activated_at": current_user.account_activated_at,
        "expires_at": expiry_date,
        "remaining_days": 90
    }

@router.get("/account-status", response_model=dict)
async def get_account_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the current status of a student account including activation and expiry information.
    """
    # Ensure this is a student account
    if current_user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is only for student accounts"
        )
        
    if current_user.account_activated_at is None:
        return {
            "message": "Account not yet activated",
            "user_id": current_user.user_id,
            "is_active": current_user.is_active,
            "activated_at": None,
            "expires_at": None,
            "remaining_days": None
        }
    
    # Calculate expiry date and remaining days
    expiry_date = current_user.account_activated_at + timedelta(days=90)
    remaining_days = (expiry_date - get_vietnam_time().replace(tzinfo=None)).days
    
    # Determine if account is expired
    account_status = "active" if remaining_days > 0 else "expired"
    
    # Update is_active in database if needed
    if account_status == "expired" and current_user.is_active:
        current_user.is_active = False
        db.commit()
    elif account_status == "active" and not current_user.is_active:
        current_user.is_active = True
        db.commit()
    
    return {
        "message": f"Account is {account_status}",
        "user_id": current_user.user_id,
        "is_active": current_user.is_active,
        "activated_at": current_user.account_activated_at,
        "expires_at": expiry_date,
        "remaining_days": max(0, remaining_days)
    }

 
