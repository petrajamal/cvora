from sqlalchemy import Column, String, Text, ForeignKey, DateTime, Boolean
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id              = Column(String, primary_key=True, index=True)
    email           = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name       = Column(String, nullable=True)
    created_at      = Column(DateTime, server_default=func.now(), nullable=True)
    is_deleted      = Column(Boolean, default=False, nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id                 = Column(String, primary_key=True, index=True)
    user_id            = Column(String, ForeignKey("users.id"), nullable=True)
    status             = Column(String, nullable=False)
    filename           = Column(String, nullable=True)
    file_path          = Column(String, nullable=True)
    extracted_text     = Column(Text, nullable=True)
    structured_data    = Column(Text, nullable=True)
    ai_structured_data = Column(Text, nullable=True)
    matched_jobs       = Column(Text, nullable=True)
    user_preferences   = Column(Text, nullable=True)
    candidate_profile  = Column(Text, nullable=True)
    generated_latex    = Column(Text, nullable=True)
    is_cv_approved     = Column(String, nullable=True)
    generated_tex_path = Column(Text, nullable=True)
    generated_pdf_path = Column(Text, nullable=True)
    status_message     = Column(Text, nullable=True)
    display_name       = Column(String, nullable=True)
    cv_type            = Column(String, nullable=True)  # "upload" | "builder"
    created_at         = Column(DateTime, server_default=func.now(), nullable=True)


class LikedJob(Base):
    """Jobs a user has liked from their results page."""
    __tablename__ = "liked_jobs"

    id          = Column(String, primary_key=True, index=True)
    user_id     = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    job_url     = Column(String, nullable=True)
    job_title   = Column(String, nullable=True)
    job_company = Column(String, nullable=True)
    job_location= Column(String, nullable=True)
    match_score = Column(String, nullable=True)
    created_at  = Column(DateTime, server_default=func.now(), nullable=True)


class PasswordResetToken(Base):
    """Short-lived, single-use tokens for password reset.
    Store only the SHA-256 hash — never the raw token."""
    __tablename__ = "password_reset_tokens"

    id         = Column(String, primary_key=True, index=True)
    user_id    = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    used       = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=True)
