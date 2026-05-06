import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Set DATABASE_URL in .env to switch to PostgreSQL:
# DATABASE_URL=postgresql://user:password@localhost:5432/cvmatcher
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./jobs.db")

if DATABASE_URL.startswith("postgresql"):
    engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20)
else:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
