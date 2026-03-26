from sqlalchemy.orm import Session
from app.database import SessionLocal, engine
from app.models.models import User
from datetime import datetime
from passlib.context import CryptContext
import sys

# Password hashing configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_admin(db: Session, username: str, email: str, password: str):
    # Check if admin already exists
    existing_user = db.query(User).filter(
        (User.username == username) | (User.email == email)
    ).first()
    
    if existing_user:
        print("Error: An admin with this username or email already exists")
        return False
    
    # Hash the password
    hashed_password = pwd_context.hash(password)
    
    # Create new admin user
    admin = User(
        username=username,
        email=email,
        password=hashed_password,
        role="admin",
        is_active=True,
        status="offline",
        created_at=datetime.utcnow()
    )
    
    try:
        db.add(admin)
        db.commit()
        db.refresh(admin)
        print(f"Admin user '{username}' created successfully!")
        return True
    except Exception as e:
        print(f"Error creating admin user: {str(e)}")
        db.rollback()
        return False

def main():
    if len(sys.argv) != 4:
        print("Usage: python create_admin.py <username> <email> <password>")
        sys.exit(1)
        
    username = sys.argv[1]
    email = sys.argv[2]
    password = sys.argv[3]
    
    db = SessionLocal()
    try:
        create_admin(db, username, email, password)
    finally:
        db.close()

if __name__ == "__main__":
    main()