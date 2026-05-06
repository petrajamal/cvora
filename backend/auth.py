import os
import re
import secrets
import hashlib
import bcrypt
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

SECRET_KEY = os.getenv("SECRET_KEY", "changeme-set-a-real-secret-in-production")
ALGORITHM  = "HS256"
TOKEN_EXPIRE_HOURS        = 24
RESET_TOKEN_EXPIRE_MINUTES = 60

bearer_scheme = HTTPBearer()


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def validate_password_strength(password: str):
    """Raise HTTP 400 if password doesn't meet strength requirements."""
    errors = []
    if len(password) < 8:
        errors.append("at least 8 characters")
    if not re.search(r"[A-Z]", password):
        errors.append("at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("at least one lowercase letter")
    if not re.search(r"\d", password):
        errors.append("at least one number")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]", password):
        errors.append("at least one special character (!@#$%^&* etc.)")
    if errors:
        raise HTTPException(
            status_code=400,
            detail=f"Password must contain: {', '.join(errors)}"
        )


# ── Password-reset token helpers ──────────────────────────────────────────────
# Strategy: generate a cryptographically random raw token, send it to the user
# (via email in production), but persist only its SHA-256 hash.  Even if the
# database is breached, the stored hashes cannot be used to reset passwords.

def generate_reset_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hash).  Store the hash; deliver the raw token."""
    raw = secrets.token_urlsafe(32)
    return raw, _hash_token(raw)


def hash_reset_token(raw: str) -> str:
    return _hash_token(raw)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def reset_token_expiry() -> datetime:
    return datetime.utcnow() + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str:
    """Return user_id from a valid token, or raise 401."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    return decode_access_token(credentials.credentials)
