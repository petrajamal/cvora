import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Set DATABASE_URL in .env to switch to PostgreSQL:
# DATABASE_URL=postgresql://user:password@localhost:5432/cvmatcher
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./jobs.db")

# Railway provides postgres:// but SQLAlchemy 2.x requires postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

print(f"[DB] Using: {DATABASE_URL[:30]}...", flush=True)

if DATABASE_URL.startswith("postgresql"):
    engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20)
else:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def run_migrations():
    """Add new columns to existing tables without dropping data."""
    migrations = [
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS display_name VARCHAR",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cv_type VARCHAR",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token_hash VARCHAR",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS last_heartbeat TIMESTAMP",
        # Auto-verify users who existed before email verification was introduced
        # (they have no verification_token_hash, so they were never sent a token)
        "UPDATE users SET email_verified = TRUE WHERE email_verified = FALSE AND verification_token_hash IS NULL",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(__import__("sqlalchemy").text(sql))
                conn.commit()
            except Exception:
                pass  # column already exists or SQLite (no IF NOT EXISTS support)
