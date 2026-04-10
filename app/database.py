from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
import time
import logging
import os
from sqlalchemy import text  # Add this import at the top


# Database URL from environment variable
DATABASE_URL = os.getenv("DATABASE_URL", "mysql+pymysql://root:root@localhost/ielts_practice_db")

engine = create_engine(
    DATABASE_URL,
    pool_size=5,           # Small pool = faster startup on Koyeb
    max_overflow=10,       # Allow bursts but don't over-allocate
    pool_timeout=30,       # Keep fast failure detection
    pool_recycle=900,      # Recycle every 15 min (TiDB drops idle connections)
    pool_pre_ping=True,    # Validate connections before use
    echo=False,            # Keep echo disabled in production
    connect_args={
        'connect_timeout': 10,   # Faster failure on unreachable DB
        'read_timeout': 60,      # Read timeout in seconds
        'write_timeout': 60,     # Write timeout in seconds
        'charset': 'utf8mb4',    # Ensure proper charset
        'autocommit': False      # Explicit autocommit setting
    }
)

# Create a configured "SessionLocal" class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()

# Dependency to get the database session with retry logic
def get_db():
    db = None
    try:
        db = SessionLocal()
        yield db
    finally:
        if db is not None:
            db.close()
