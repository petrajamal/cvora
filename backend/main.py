from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Depends, Request
from fastapi.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
import uuid
import os
import json
import re
import time
import hashlib
import secrets
import requests
import hmac
from collections import defaultdict
from datetime import datetime

from latex_gen import (
    generate_latex_cv, compile_to_pdf, compile_to_pdf_checked,
    compute_allow_two_pages, estimate_bullet_lines, last_line_word_count,
    estimate_cv_overlong,
)

from database import Base, engine, SessionLocal
import r2
from models import Job, User, PasswordResetToken, LikedJob
from fastapi.middleware.cors import CORSMiddleware
from auth import (
    hash_password, verify_password,
    create_access_token, get_current_user_id, decode_access_token,
    validate_password_strength,
    generate_reset_token, hash_reset_token, reset_token_expiry,
)

# ── Input sanitization ────────────────────────────────────────────────────────
# SQLAlchemy ORM uses parameterized queries throughout, so SQL injection is
# already prevented at the DB layer.  These helpers add a second layer of
# defence by stripping null bytes and enforcing length limits before any
# user-supplied string touches application logic.

def sanitize(value, max_len: int = 500) -> str:
    """Strip, null-byte-clean, and truncate an untrusted string."""
    if value is None:
        return ""
    return str(value).replace("\x00", "").replace("\r", "").strip()[:max_len]


# ── In-memory rate limiter ────────────────────────────────────────────────────
# For production replace with Redis + slowapi.  This is intentionally simple:
# it resets on server restart and doesn't scale across multiple processes.

_rate_store: dict[str, list[float]] = defaultdict(list)

def rate_limit(key: str, max_calls: int = 10, window: int = 60):
    """Raise 429 if `key` has made more than `max_calls` in `window` seconds."""
    now = time.monotonic()
    bucket = _rate_store[key]
    _rate_store[key] = [t for t in bucket if now - t < window]
    if len(_rate_store[key]) >= max_calls:
        raise HTTPException(
            status_code=429,
            detail="Too many requests — please wait and try again.",
        )
    _rate_store[key].append(now)


# ── Validation helpers ────────────────────────────────────────────────────────

EMAIL_RE = re.compile(r'^[\w\.\+\-]+@[\w\-]+\.[a-zA-Z]{2,}$')
PHONE_RE = re.compile(r'^[+\d][\d\s\-\(\)]{5,18}$')
MONTH_RE = re.compile(r'^\d{4}-\d{2}$')
URL_RE   = re.compile(r'^https?://', re.IGNORECASE)

# Block common injection probe characters that have no place in plain-text fields
_INJECTION_RE = re.compile(r'[\x00<>\'";`]')

def assert_safe_text(value: str, field: str):
    """Raise 400 if the value contains characters used in injection attacks."""
    if _INJECTION_RE.search(value):
        raise HTTPException(
            status_code=400,
            detail=f"{field} contains disallowed characters.",
        )


def get_month_key() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def is_pro(user: User) -> bool:
    return user.subscription_tier == "pro" and user.subscription_status == "active"


def check_and_increment_uploads(user: User, db) -> tuple[bool, str]:
    mk = get_month_key()
    if user.uploads_month_key != mk:
        user.uploads_this_month = 0
        user.uploads_month_key = mk
    if is_pro(user):
        if (user.uploads_this_month or 0) >= 50:
            return False, "monthly_limit"
    else:
        if (user.uploads_this_month or 0) >= 2:
            return False, "upgrade_required"
    user.uploads_this_month = (user.uploads_this_month or 0) + 1
    db.commit()
    return True, "ok"


def check_and_increment_builds(user: User, db) -> tuple[bool, str]:
    mk = get_month_key()
    if user.builds_month_key != mk:
        user.cv_builds_this_month = 0
        user.builds_month_key = mk
    if not is_pro(user) and (user.cv_builds_this_month or 0) >= 3:
        return False, "upgrade_required"
    user.cv_builds_this_month = (user.cv_builds_this_month or 0) + 1
    db.commit()
    return True, "ok"


def check_and_increment_searches(user: User, db) -> tuple[bool, str]:
    mk = get_month_key()
    if user.searches_month_key != mk:
        user.job_searches_this_month = 0
        user.searches_month_key = mk
    if not is_pro(user) and (user.job_searches_this_month or 0) >= 5:
        return False, "upgrade_required"
    user.job_searches_this_month = (user.job_searches_this_month or 0) + 1
    db.commit()
    return True, "ok"


def validate_candidate_profile(profile: dict):
    errors = []

    full_name = sanitize(profile.get("full_name"), 100)
    email     = sanitize(profile.get("email"), 120)
    phone     = sanitize(profile.get("phone"), 20)

    if not full_name:
        errors.append("full_name is required")
    elif len(full_name) > 100:
        errors.append("full_name exceeds 100 characters")

    if not email:
        errors.append("email is required")
    elif not EMAIL_RE.match(email):
        errors.append(f"email is invalid: {email!r}")

    if not phone:
        errors.append("phone is required")
    elif not PHONE_RE.match(phone):
        errors.append(f"phone is invalid: {phone!r}")

    location = sanitize(profile.get("location") or "", 100)
    if len(location) > 100:
        errors.append("location exceeds 100 characters")

    summary = sanitize(profile.get("summary") or "", 1000)
    if len(summary) > 1000:
        errors.append("summary exceeds 1000 characters")

    setup = profile.get("setup") or {}
    target_fields_raw = setup.get("target_fields") or []
    joined = ", ".join(target_fields_raw) if isinstance(target_fields_raw, list) else str(target_fields_raw)
    if len(joined) > 200:
        errors.append("target_fields exceeds 200 characters")

    for link in (profile.get("links") or []):
        url = sanitize(link.get("url") or "", 300)
        if url and not URL_RE.match(url):
            errors.append(f"link URL is not valid: {url!r}")
        if len(url) > 300:
            errors.append("link URL exceeds 300 characters")
        if len(sanitize(link.get("display") or "", 100)) > 100:
            errors.append("link display text exceeds 100 characters")

    education = profile.get("education") or []
    if not education:
        errors.append("at least one education entry is required")
    for edu in education:
        if not sanitize(edu.get("institution") or "", 200):
            errors.append("education entry missing institution")
        if not sanitize(edu.get("degree") or "", 150):
            errors.append("education entry missing degree")
        for field in ("start_date", "end_date"):
            val = sanitize(edu.get(field) or "", 7)
            if val and not MONTH_RE.match(val):
                errors.append(f"education {field} must be YYYY-MM, got {val!r}")

    if not (profile.get("skills") or []):
        errors.append("at least one skill is required")

    for exp in (profile.get("work_experience") or []):
        if not sanitize(exp.get("organization") or "", 200):
            errors.append("work_experience entry missing organization")
        if not sanitize(exp.get("position") or "", 150):
            errors.append("work_experience entry missing position")
        for field in ("start_date", "end_date"):
            val = sanitize(exp.get(field) or "", 7)
            if val and not MONTH_RE.match(val):
                errors.append(f"work_experience {field} must be YYYY-MM, got {val!r}")

    for proj in (profile.get("projects") or []):
        if not sanitize(proj.get("title") or "", 150):
            errors.append("project entry missing title")
        link = sanitize(proj.get("link") or "", 300)
        if link and not URL_RE.match(link):
            errors.append(f"project link is not a valid URL: {link!r}")

    has_substance = bool(
        (profile.get("work_experience") or []) or
        (profile.get("projects") or []) or
        (profile.get("extracurriculars") or [])
    )
    if not has_substance:
        errors.append("at least one of work_experience, projects, or extracurriculars is required")

    if errors:
        raise HTTPException(status_code=422, detail=errors)


# ── Security headers middleware ───────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]        = "camera=(), microphone=(), geolocation=()"
        return response


# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI()

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    # "*" is safe here because the API uses JWT in headers (not cookies),
    # allow_credentials=False, so CSRF via cross-origin is not possible.
    # In production behind nginx the frontend is same-origin so CORS isn't
    # triggered anyway. Restrict via ALLOWED_ORIGINS env var if needed.
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)
from database import run_migrations
run_migrations()

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DEV_MODE = os.getenv("DEV_MODE", "true").lower() == "true"

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FRONTEND_URL   = os.getenv("FRONTEND_URL", "https://cvora.pages.dev")

LS_API_KEY          = os.getenv("LS_API_KEY", "")
LS_WEBHOOK_SECRET   = os.getenv("LS_WEBHOOK_SECRET", "")
LS_STORE_ID         = os.getenv("LS_STORE_ID", "")
LS_VARIANT_MONTHLY  = os.getenv("LS_VARIANT_MONTHLY", "")
LS_VARIANT_YEARLY   = os.getenv("LS_VARIANT_YEARLY", "")

_LS_HEADERS = {
    "Authorization": f"Bearer {LS_API_KEY}",
    "Content-Type":  "application/vnd.api+json",
    "Accept":        "application/vnd.api+json",
}

print(f"[STARTUP] RESEND={'set' if RESEND_API_KEY else 'MISSING'} FRONTEND_URL={FRONTEND_URL}", flush=True)


def _send_email(to_email: str, subject: str, html: str, label: str = "email"):
    if not RESEND_API_KEY:
        print(f"[EMAIL] RESEND_API_KEY not set — skipping {label}.", flush=True)
        return
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": "CVora <noreply@cvora.live>", "to": [to_email], "subject": subject, "html": html},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            print(f"[EMAIL] {label} sent to {to_email}", flush=True)
        else:
            print(f"[EMAIL] Resend error {resp.status_code}: {resp.text}", flush=True)
    except Exception as exc:
        print(f"[EMAIL] Failed: {exc}", flush=True)


def _send_reset_email(to_email: str, raw_token: str):
    reset_link = f"{FRONTEND_URL}/app.html?token={raw_token}"
    html = f"""
    <div style="font-family:Inter,system-ui,sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;color:#0F172A;">
      <div style="margin-bottom:24px;"><span style="background:#4F46E5;color:white;padding:6px 12px;border-radius:8px;font-weight:700;font-size:14px;">CVora</span></div>
      <h2 style="font-size:20px;font-weight:700;margin:0 0 8px;">Reset your password</h2>
      <p style="color:#475569;margin:0 0 24px;font-size:14px;">Click the button below to set a new password. This link expires in 60 minutes.</p>
      <a href="{reset_link}" style="display:inline-block;background:#4F46E5;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;">Reset Password</a>
      <p style="color:#94A3B8;margin-top:24px;font-size:12px;">If you didn't request this, you can safely ignore this email.</p>
    </div>
    """
    _send_email(to_email, "Reset your CVora password", html, label="reset")


def _send_verification_email(to_email: str, raw_token: str):
    verify_link = f"{FRONTEND_URL}/app.html?verify_token={raw_token}"
    html = f"""
    <div style="font-family:Inter,system-ui,sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;color:#0F172A;">
      <div style="margin-bottom:24px;"><span style="background:#4F46E5;color:white;padding:6px 12px;border-radius:8px;font-weight:700;font-size:14px;">CVora</span></div>
      <h2 style="font-size:20px;font-weight:700;margin:0 0 8px;">Verify your email</h2>
      <p style="color:#475569;margin:0 0 24px;font-size:14px;">Click the button below to verify your email address and activate your account.</p>
      <a href="{verify_link}" style="display:inline-block;background:#4F46E5;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;">Verify Email</a>
      <p style="color:#94A3B8;margin-top:24px;font-size:12px;">If you didn't create a CVora account, you can safely ignore this email.</p>
    </div>
    """
    _send_email(to_email, "Verify your CVora email", html, label="verification")


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.post("/register")
def register(payload: dict, request: Request):
    rate_limit(f"register:{request.client.host}", max_calls=10, window=300)

    email    = sanitize(payload.get("email") or "", 120).lower()
    password = sanitize(payload.get("password") or "", 200)

    if not email or not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")

    validate_password_strength(password)

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            raise HTTPException(status_code=409, detail="Email already registered. Try logging in instead.")

        raw_token   = secrets.token_urlsafe(32)
        token_hash  = hashlib.sha256(raw_token.encode()).hexdigest()

        user = User(
            id=str(uuid.uuid4()),
            email=email,
            hashed_password=hash_password(password),
            email_verified=DEV_MODE,
            verification_token_hash=None if DEV_MODE else token_hash,
        )
        db.add(user)
        db.commit()

        if DEV_MODE:
            token = create_access_token(user.id)
            return {"token": token, "email": user.email}
        else:
            _send_verification_email(email, raw_token)
            return {"requires_verification": True, "email": email}
    finally:
        db.close()


@app.post("/login")
def login(payload: dict, request: Request):
    rate_limit(f"login:{request.client.host}", max_calls=10, window=60)

    email    = sanitize(payload.get("email") or "", 120).lower()
    password = sanitize(payload.get("password") or "", 200)

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        if not user.email_verified:
            raise HTTPException(status_code=403, detail="EMAIL_NOT_VERIFIED")

        token = create_access_token(user.id)
        return {"token": token, "email": user.email}
    finally:
        db.close()


@app.post("/verify-email")
def verify_email(payload: dict):
    raw_token = sanitize(payload.get("token") or "", 200)
    if not raw_token:
        raise HTTPException(status_code=400, detail="Token required")

    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.verification_token_hash == token_hash).first()
        if not user:
            raise HTTPException(status_code=400, detail="Invalid or expired verification link.")
        user.email_verified = True
        user.verification_token_hash = None
        db.commit()
        token = create_access_token(user.id)
        return {"token": token, "email": user.email}
    finally:
        db.close()


@app.post("/resend-verification")
def resend_verification(payload: dict, request: Request):
    rate_limit(f"resend:{request.client.host}", max_calls=3, window=300)
    email = sanitize(payload.get("email") or "", 120).lower()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user and not user.email_verified:
            raw_token  = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            user.verification_token_hash = token_hash
            db.commit()
            _send_verification_email(email, raw_token)
    finally:
        db.close()
    return {"ok": True}


# ── Forgot / reset password ───────────────────────────────────────────────────

@app.post("/forgot-password")
def forgot_password(payload: dict, request: Request):
    """
    Always returns a generic 200 to prevent email-enumeration attacks.
    In DEV_MODE the raw token is included in the response so you can test
    without an SMTP server.  Remove that field before going to production
    and wire up a real email sender instead.
    """
    rate_limit(f"forgot:{request.client.host}", max_calls=3, window=900)

    email = sanitize(payload.get("email") or "", 120).lower()
    generic = {"message": "If that email is registered you will receive a reset link shortly."}

    if not email or not EMAIL_RE.match(email):
        return generic

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        print(f"[FORGOT] email={email} found={user is not None}", flush=True)
        if not user:
            return generic  # don't reveal whether the email exists

        # Invalidate any previous unused tokens for this user
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used == False,
        ).delete(synchronize_session=False)

        raw_token, token_hash = generate_reset_token()
        record = PasswordResetToken(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=token_hash,
            expires_at=reset_token_expiry(),
            used=False,
        )
        db.add(record)
        db.commit()

        print(f"[DEV] Password reset token for {email}: {raw_token}")
        _send_reset_email(email, raw_token)

        if DEV_MODE:
            return {**generic, "dev_token": raw_token}
        return generic
    finally:
        db.close()


@app.post("/reset-password")
def reset_password(payload: dict, request: Request):
    rate_limit(f"reset:{request.client.host}", max_calls=10, window=300)

    raw_token    = sanitize(payload.get("token") or "", 200)
    new_password = sanitize(payload.get("new_password") or payload.get("password") or "", 200)

    if not raw_token or not new_password:
        raise HTTPException(status_code=400, detail="Token and new password are required.")

    validate_password_strength(new_password)

    token_hash = hash_reset_token(raw_token)

    db = SessionLocal()
    try:
        record = db.query(PasswordResetToken).filter(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used == False,
        ).first()

        if not record or record.expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

        user = db.query(User).filter(User.id == record.user_id).first()
        if not user:
            raise HTTPException(status_code=400, detail="User not found.")

        user.hashed_password = hash_password(new_password)
        record.used = True
        db.commit()

        return {"message": "Password reset successful. You can now log in."}
    finally:
        db.close()


# ── Profile / history routes (protected) ─────────────────────────────────────

@app.get("/profile")
def get_profile(user_id: str = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return {
            "email":                  user.email,
            "created_at":             user.created_at.isoformat() if user.created_at else None,
            "subscription_tier":      user.subscription_tier or "free",
            "subscription_status":    user.subscription_status or "inactive",
            "uploads_this_month":     user.uploads_this_month or 0,
            "cv_builds_this_month":   user.cv_builds_this_month or 0,
            "job_searches_this_month": user.job_searches_this_month or 0,
        }
    finally:
        db.close()


@app.get("/my-jobs")
def my_jobs(user_id: str = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        jobs = (
            db.query(Job)
            .filter(Job.user_id == user_id)
            .order_by(Job.created_at.desc())
            .all()
        )
        result = []
        for job in jobs:
            matched = json.loads(job.matched_jobs) if job.matched_jobs else []
            raw_profile = (
                json.loads(job.ai_structured_data) if job.ai_structured_data else
                json.loads(job.candidate_profile)  if job.candidate_profile  else {}
            )
            top = matched[0] if matched else None
            cv_type = job.cv_type or ("upload" if job.file_path else "builder")
            result.append({
                "job_id":           job.id,
                "status":           job.status,
                "status_message":   job.status_message,
                "filename":         job.filename,
                "display_name":     job.display_name or job.filename or "Untitled CV",
                "cv_type":          cv_type,
                "candidate_name":   raw_profile.get("full_name"),
                "match_count":      len(matched),
                "top_match":        {"title": top.get("title"), "company": top.get("company"), "match_score": top.get("match_score")} if top else None,
                "has_pdf":          bool(job.generated_pdf_path),
                "has_latex":        bool(job.generated_latex),
                "has_upload":       bool(job.file_path),
                "candidate_profile": job.candidate_profile,
                "created_at":       job.created_at.isoformat() if job.created_at else None,
            })
        return {"jobs": result}
    finally:
        db.close()


@app.patch("/job/{job_id}/rename")
def rename_job(job_id: str, payload: dict, user_id: str = Depends(get_current_user_id)):
    name = sanitize(payload.get("name") or "", 120).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        job.display_name = name
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.delete("/job/{job_id}")
def delete_job(job_id: str, user_id: str = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        # Collect URLs from matched jobs so we can cascade-delete liked jobs
        matched_urls: set[str] = set()
        if job.matched_jobs:
            try:
                for m in json.loads(job.matched_jobs):
                    url = (m.get("url") or m.get("job_url") or "").strip()
                    if url:
                        matched_urls.add(url)
            except Exception:
                pass
        deleted_likes = 0
        if matched_urls:
            rows = (
                db.query(LikedJob)
                .filter(LikedJob.user_id == user_id, LikedJob.job_url.in_(matched_urls))
                .all()
            )
            deleted_likes = len(rows)
            for row in rows:
                db.delete(row)
        # Delete R2 objects
        for key in [job.file_path, job.generated_pdf_path]:
            if key:
                r2.delete(key)
        db.delete(job)
        db.commit()
        return {"ok": True, "deleted_likes": deleted_likes}
    finally:
        db.close()


@app.post("/job/{job_id}/cancel")
def cancel_job(job_id: str, user_id: str = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in ("pending", "processing", "pending_matching"):
            raise HTTPException(status_code=400, detail="Job is not in a cancellable state")
        job.status = "failed"
        job.status_message = "Cancelled by user."
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.post("/job/{job_id}/cancel-beacon")
async def cancel_job_beacon(job_id: str, request: Request):
    """sendBeacon-compatible cancel: reads token from JSON body (no auth header support in beacons)."""
    try:
        body = await request.json()
        token = body.get("token", "")
        user_id = decode_access_token(token)
    except Exception:
        return Response(status_code=204)
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if job and job.status in ("pending", "processing", "pending_matching"):
            job.status = "failed"
            job.status_message = "Cancelled — page closed or refreshed."
            db.commit()
    finally:
        db.close()
    return Response(status_code=204)


@app.post("/job/{job_id}/heartbeat")
def job_heartbeat(job_id: str, user_id: str = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if job and job.status in ("pending", "processing", "pending_matching"):
            job.last_heartbeat = datetime.utcnow()
            db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.delete("/delete-account")
def delete_account(user_id: str = Depends(get_current_user_id)):
    """Soft-delete the user account. Sets is_deleted=True and removes all job/liked data."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Delete liked jobs
        db.query(LikedJob).filter(LikedJob.user_id == user_id).delete()

        # Delete all jobs (and their R2 files best-effort)
        jobs = db.query(Job).filter(Job.user_id == user_id).all()
        for j in jobs:
            if j.file_path:
                try: r2.delete(j.file_path)
                except Exception: pass
            if j.generated_pdf_path:
                try: r2.delete(j.generated_pdf_path)
                except Exception: pass
            db.delete(j)

        # Soft-delete the user
        user.is_deleted = True
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.get("/download-upload/{job_id}")
def download_upload(job_id: str, user_id: str = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        if not job.file_path:
            raise HTTPException(status_code=404, detail="No uploaded file for this CV.")
        try:
            pdf_bytes = r2.download_bytes(job.file_path)
        except Exception:
            raise HTTPException(status_code=404, detail="File not found in storage.")
        fname = job.display_name or job.filename or "cv.pdf"
        if not fname.endswith(".pdf"):
            fname += ".pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={fname}"},
        )
    finally:
        db.close()


# ── Like routes ──────────────────────────────────────────────────────────────

@app.post("/like-job")
def like_job(payload: dict, user_id: str = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        job_url   = sanitize(payload.get("job_url", ""), 500)
        job_title = sanitize(payload.get("job_title", ""), 200)
        company   = sanitize(payload.get("job_company", ""), 200)
        location  = sanitize(payload.get("job_location", ""), 200)
        score     = sanitize(str(payload.get("match_score", "")), 20)

        # Upsert — ignore if already liked
        existing = db.query(LikedJob).filter(
            LikedJob.user_id == user_id,
            LikedJob.job_url == job_url,
        ).first()
        if existing:
            return {"status": "already_liked"}

        liked = LikedJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            job_url=job_url,
            job_title=job_title,
            job_company=company,
            job_location=location,
            match_score=score,
        )
        db.add(liked)
        db.commit()
        return {"status": "liked"}
    finally:
        db.close()


@app.delete("/like-job")
def unlike_job(payload: dict, user_id: str = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        job_url = sanitize(payload.get("job_url", ""), 500)
        db.query(LikedJob).filter(
            LikedJob.user_id == user_id,
            LikedJob.job_url == job_url,
        ).delete(synchronize_session=False)
        db.commit()
        return {"status": "unliked"}
    finally:
        db.close()


@app.get("/liked-jobs")
def get_liked_jobs(user_id: str = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        liked = db.query(LikedJob).filter(
            LikedJob.user_id == user_id
        ).order_by(LikedJob.created_at.desc()).all()
        return [
            {
                "id":          l.id,
                "job_url":     l.job_url,
                "job_title":   l.job_title,
                "job_company": l.job_company,
                "job_location":l.job_location,
                "match_score": l.match_score,
                "created_at":  l.created_at.isoformat() if l.created_at else None,
            }
            for l in liked
        ]
    finally:
        db.close()


# ── CV routes (protected) ─────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "API is running"}

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload-cv")
async def upload_cv(
    file: UploadFile = File(...),
    modes: str = Form(""),
    relocation_locations: str = Form(""),
    user_id: str = Depends(get_current_user_id),
    request: Request = None,
):
    rate_limit(f"upload:{user_id}", max_calls=20, window=3600)

    allowed_mime = {"application/pdf", "application/x-pdf"}
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")
    if file.content_type and file.content_type not in allowed_mime:
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        allowed, reason = check_and_increment_uploads(user, db)
        if not allowed:
            if reason == "upgrade_required":
                raise HTTPException(status_code=402, detail={"detail": "upgrade_required", "limit": "free_upload_limit"})
            raise HTTPException(status_code=429, detail={"detail": "monthly_limit_reached", "limit": "pro_upload_limit", "contact": "cvora.contact@gmail.com"})

        job_id    = str(uuid.uuid4())
        safe_name = re.sub(r"[^\w\.\-]", "_", file.filename)[:100]
        file_path = f"{UPLOAD_FOLDER}/{job_id}_{safe_name}"

        content = await file.read()
        if len(content) > 10 * 1024 * 1024:  # 10 MB cap
            raise HTTPException(status_code=413, detail="File is too large (max 10 MB).")
        if not content.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="File does not appear to be a valid PDF.")

        r2.upload_bytes(file_path, content, content_type="application/pdf")

        preferences = {
            "modes": [sanitize(m, 50) for m in modes.split(",") if m.strip()],
            "relocation_locations": [sanitize(loc, 10) for loc in relocation_locations.split(",") if loc.strip()],
        }

        job = Job(
            id=job_id,
            user_id=user_id,
            status="pending",
            filename=safe_name,
            display_name=safe_name,
            cv_type="upload",
            file_path=file_path,
            user_preferences=json.dumps(preferences),
        )
        db.add(job)
        db.commit()
        return {"job_id": job_id, "status": "pending"}
    finally:
        db.close()


@app.post("/build-cv")
async def build_cv(
    payload: dict,
    user_id: str = Depends(get_current_user_id),
    request: Request = None,
):
    rate_limit(f"build:{user_id}", max_calls=30, window=3600)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        allowed, reason = check_and_increment_builds(user, db)
        if not allowed:
            raise HTTPException(status_code=402, detail={"detail": "upgrade_required", "limit": "free_build_limit"})

        candidate_profile = payload.get("candidate_profile")
        if not candidate_profile:
            raise HTTPException(status_code=400, detail="Missing candidate_profile")

        validate_candidate_profile(candidate_profile)

        raw_prefs = payload.get("preferences") or {}
        if raw_prefs.get("modes") is not None:
            preferences = {
                "modes": raw_prefs.get("modes") or [],
                "relocation_locations": raw_prefs.get("relocation_locations") or [],
            }
        else:
            candidate_location = sanitize(candidate_profile.get("location") or "", 100)
            preferences = {
                "modes": ["cv_location"] if candidate_location else [],
                "relocation_locations": [],
            }

        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            user_id=user_id,
            status="pending",
            cv_type="builder",
            display_name=sanitize(candidate_profile.get("full_name") or "", 120) or "Built CV",
            candidate_profile=json.dumps(candidate_profile),
            user_preferences=json.dumps(preferences),
        )
        db.add(job)
        db.commit()
        return {"job_id": job_id, "status": "pending"}
    finally:
        db.close()


@app.get("/results/{job_id}")
def get_results(
    job_id: str,
    user_id: str = Depends(get_current_user_id),
):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        return {
            "job_id":             job.id,
            "status":             job.status,
            "status_message":     job.status_message,
            "filename":           job.filename,
            "user_preferences":   json.loads(job.user_preferences)   if job.user_preferences   else None,
            "structured_data":    json.loads(job.structured_data)    if job.structured_data    else None,
            "ai_structured_data": json.loads(job.ai_structured_data) if job.ai_structured_data else None,
            "matched_jobs":       json.loads(job.matched_jobs)        if job.matched_jobs        else [],
            "generated_latex":    job.generated_latex,
            "generated_tex_path": job.generated_tex_path,
            "generated_pdf_path": job.generated_pdf_path,
            "is_cv_approved":     job.is_cv_approved,
        }
    finally:
        db.close()


@app.get("/download-cv/{job_id}")
def download_cv(
    job_id: str,
    user_id: str = Depends(get_current_user_id),
):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        if not job.generated_pdf_path:
            raise HTTPException(status_code=404, detail="PDF not ready yet")
        try:
            pdf_bytes = r2.download_bytes(job.generated_pdf_path)
        except Exception:
            raise HTTPException(status_code=404, detail="PDF not found in storage")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=cv.pdf"},
        )
    finally:
        db.close()


@app.post("/save-cv-only")
async def save_cv_only(payload: dict, user_id: str = Depends(get_current_user_id)):
    """Save or update a built CV. Pass job_id to update an existing entry."""
    candidate_profile = payload.get("candidate_profile")
    display_name = sanitize(payload.get("display_name") or "My CV", 120)
    job_id_to_update = payload.get("job_id")
    if not candidate_profile:
        raise HTTPException(status_code=400, detail="Missing candidate_profile")

    cleaned_profile  = clean_profile_input(candidate_profile)
    enhanced_profile = await auto_enhance_cv_descriptions(cleaned_profile)

    try:
        latex_source, pdf_bytes, _ = await build_one_page_cv(enhanced_profile)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"CV generation failed: {exc}")

    db = SessionLocal()
    try:
        if job_id_to_update:
            job = db.query(Job).filter(Job.id == job_id_to_update, Job.user_id == user_id).first()
            if not job:
                raise HTTPException(status_code=404, detail="CV not found")
            if pdf_bytes:
                pdf_path = f"generated/{job_id_to_update}.pdf"
                r2.upload_bytes(pdf_path, pdf_bytes, content_type="application/pdf")
                job.generated_pdf_path = pdf_path
            job.generated_latex = latex_source
            job.candidate_profile = json.dumps(candidate_profile)
            db.commit()
            return {"job_id": job_id_to_update, "has_pdf": bool(pdf_bytes)}
        else:
            job_id = str(uuid.uuid4())
            pdf_path = None
            if pdf_bytes:
                pdf_path = f"generated/{job_id}.pdf"
                r2.upload_bytes(pdf_path, pdf_bytes, content_type="application/pdf")
            job = Job(
                id=job_id,
                user_id=user_id,
                filename=display_name,
                display_name=display_name,
                cv_type="built",
                status="cv_generated",
                status_message="CV saved.",
                generated_latex=latex_source,
                generated_pdf_path=pdf_path,
                candidate_profile=json.dumps(candidate_profile),
            )
            db.add(job)
            db.commit()
            return {"job_id": job_id, "has_pdf": bool(pdf_bytes)}
    finally:
        db.close()


@app.get("/download-cv/{job_id}/latex")
async def download_cv_latex(job_id: str, user_id: str = Depends(get_current_user_id)):
    """Download the LaTeX source (.tex) for a built CV, regenerated fresh so it matches the viewer."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="Not found")
        name = (job.display_name or job.filename or "cv").replace(" ", "_")
        # Return the stored latex that was actually compiled — guaranteed to match the PDF.
        # Only regenerate if the stored latex is missing (old CVs created before storage was added).
        latex_source = job.generated_latex or ""
        if not latex_source and job.candidate_profile:
            try:
                raw_profile = json.loads(job.candidate_profile)
                cleaned = clean_profile_input(raw_profile)
                enhanced = await auto_enhance_cv_descriptions(cleaned)
                latex_source, _, _ = await build_one_page_cv(enhanced)
            except Exception:
                latex_source = ""
        if not latex_source:
            raise HTTPException(status_code=404, detail="LaTeX source not available for this CV")
        return Response(
            content=latex_source.encode("utf-8"),
            media_type="application/x-tex",
            headers={"Content-Disposition": f'attachment; filename="{name}.tex"'},
        )
    finally:
        db.close()


@app.get("/view-cv/{job_id}")
def view_cv(job_id: str, user_id: str = Depends(get_current_user_id)):
    """Return the PDF inline for the in-app viewer popup."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="Not found")
        if not job.generated_pdf_path:
            raise HTTPException(status_code=404, detail="PDF not available")
        pdf_bytes = r2.download_bytes(job.generated_pdf_path)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline; filename=cv.pdf"},
        )
    finally:
        db.close()


import unicodedata

_BULLET_CHARS = re.compile(r"^[\s•\-–—*▪▸►◦·▷>]+")
_TRAILING_PUNCT = re.compile(r"[\s.,;:!?]+$")
_EMOJI_RE = re.compile(
    "[\U00010000-\U0010ffff"
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)


def _clean_text(t: str) -> str:
    """Strip bullet chars, emojis, trailing punctuation, and collapse whitespace."""
    t = _EMOJI_RE.sub("", t)
    t = _BULLET_CHARS.sub("", t)
    t = _TRAILING_PUNCT.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def clean_profile_input(profile: dict) -> dict:
    """
    Pure-Python pass that strips bullet characters, emojis, trailing
    punctuation, and extra whitespace from all free-text fields.
    """
    import copy
    p = copy.deepcopy(profile)

    def _clean_list(lst):
        if not isinstance(lst, list):
            return lst
        return [_clean_text(b) if isinstance(b, str) else b for b in lst]

    if p.get("summary"):
        p["summary"] = _clean_text(p["summary"])

    for section in ("work_experience", "education", "projects", "extracurriculars", "awards"):
        for entry in (p.get(section) or []):
            if isinstance(entry.get("description"), list):
                entry["description"] = [
                    b for b in _clean_list(entry["description"]) if b
                ]

    return p


async def auto_enhance_cv_descriptions(profile: dict) -> dict:
    """
    Fix only bullets that are fewer than 6 words or clearly junk/placeholder.
    All other text is passed through unchanged to prevent hallucination on
    repeated saves.
    """
    import copy
    from openai import AsyncOpenAI

    if not os.getenv("OPENAI_API_KEY"):
        return profile

    p = copy.deepcopy(profile)
    items = []  # [(original_text, context_str, setter_fn)]

    def _collect(desc_list, ctx):
        if not isinstance(desc_list, list):
            return
        for i in range(len(desc_list)):
            b = desc_list[i]
            if isinstance(b, str) and b.strip():
                items.append((b, ctx, lambda v, d=desc_list, j=i: d.__setitem__(j, v)))

    summary = (p.get("summary") or "").strip()
    if summary:
        name = p.get("full_name", "applicant")
        items.append((summary, f"Professional summary for {name}",
                       lambda v: p.update({"summary": v})))

    for exp in (p.get("work_experience") or []):
        _collect(exp.get("description") or [],
                 f"{exp.get('position','role')} at {exp.get('organization','company')}")

    for edu in (p.get("education") or []):
        _collect(edu.get("description") or [],
                 f"{edu.get('degree','study')} at {edu.get('institution','school')}")

    for proj in (p.get("projects") or []):
        _collect(proj.get("description") or [],
                 f"Project: {proj.get('title','project')}")

    for ex in (p.get("extracurriculars") or []):
        _collect(ex.get("description") or [],
                 f"{ex.get('role','')} at {ex.get('organization','')}")

    if not items:
        return p

    entries = "\n\n".join(
        f"{i+1}. [Context: {ctx}]\n{text}"
        for i, (text, ctx, _) in enumerate(items)
    )
    prompt = (
        "You are a professional CV editor. Improve each numbered bullet point below.\n\n"
        "Rules:\n"
        "1. Fix all grammar, spelling, and punctuation.\n"
        "2. Use a consistent professional tone throughout — active voice, strong action verbs, "
        "past tense for past roles.\n"
        "3. Make wording concise and CV-appropriate. Remove filler and placeholder phrases.\n"
        "4. If a bullet is nonsensical or clearly a placeholder (e.g. 'slayed', 'aaa', 'test'), "
        "replace it with a short professional bullet appropriate for the given context. "
        "Do NOT invent specific metrics or facts.\n"
        "5. NEVER add new bullet points. Return exactly one line per numbered item.\n"
        "6. Do NOT change the factual content — only improve language and tone.\n\n"
        "Return ONLY the numbered items in the same order, one per line:\n"
        "1. improved text\n2. improved text\n\n"
        + entries
    )

    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = await client.responses.create(model="gpt-4o-mini", input=prompt)
        for num_str, text in re.findall(
            r"^(\d+)\.\s+(.*?)(?=\n\d+\.|\Z)",
            resp.output_text.strip(),
            re.DOTALL | re.MULTILINE,
        ):
            idx = int(num_str) - 1
            if 0 <= idx < len(items):
                items[idx][2](re.sub(r"\s+", " ", text).strip())
    except Exception:
        return profile  # fall back to original on any failure

    return p


async def apply_line_rules(profile: dict, long_cv: bool = False) -> dict:
    """
    Rule 1  — if an entry's bullets exceed 6 printed lines total, combine and
              summarise them to fit within 6 lines.
    Rule 1b — (long_cv only) pair-combine 1-line bullets in entries with ≥6
              single-line bullets.
    Rule 2  — widow fix: if a bullet has exactly 1 word on its last line,
              ask GPT to make the bullet ONE WORD LONGER so the trailing word
              merges onto the previous line. Repeated up to 3 passes.
    """
    import copy
    from openai import AsyncOpenAI

    MAX_ENTRY_LINES = 6
    SECTIONS = ("work_experience", "education", "projects", "extracurriculars", "awards")

    p = copy.deepcopy(profile)

    if not os.getenv("OPENAI_API_KEY"):
        return p

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # ── Rule 1: summarise overlong entries ───────────────────────────────────
    to_summarise = []
    for section in SECTIONS:
        for entry in (p.get(section) or []):
            desc = entry.get("description")
            if not isinstance(desc, list) or not desc:
                continue
            total = sum(estimate_bullet_lines(b) for b in desc)
            if total > MAX_ENTRY_LINES:
                to_summarise.append(
                    (list(desc), lambda v, e=entry: e.__setitem__("description", v))
                )

    if to_summarise:
        sections_text = "\n\n".join(
            "ENTRY {}:\n{}".format(i + 1, "\n".join(f"- {b}" for b in bullets))
            for i, (bullets, _) in enumerate(to_summarise)
        )
        prompt = (
            "The bullet lists below are from CV entries and are too long to fit on one page. "
            "For each ENTRY, combine and condense the bullets into a shorter list that:\n"
            "- Preserves all key responsibilities and achievements from the original\n"
            "- Keeps each bullet to ~100 characters so it prints on 1 line (2 lines max per bullet)\n"
            "- Keeps the total printed lines for that entry to {} or fewer\n"
            "- Uses strong action verbs; does not invent new information\n\n"
            "Return in exactly this format (no extra text):\n"
            "ENTRY 1:\n- bullet\n- bullet\n\nENTRY 2:\n- bullet\n\n"
            "{}"
        ).format(MAX_ENTRY_LINES, sections_text)

        try:
            resp = await client.responses.create(model="gpt-4o-mini", input=prompt)
            current_idx = None
            current_bullets: list[str] = []

            def _flush():
                if current_idx is not None and 0 <= current_idx < len(to_summarise):
                    if current_bullets:
                        to_summarise[current_idx][1](list(current_bullets))

            for line in resp.output_text.strip().splitlines():
                line = line.strip()
                m = re.match(r"^ENTRY\s+(\d+):?$", line, re.IGNORECASE)
                if m:
                    _flush()
                    current_idx = int(m.group(1)) - 1
                    current_bullets = []
                elif line.startswith(("-", "•")) and current_idx is not None:
                    bullet = re.sub(r"^[-•]\s*", "", line).strip()
                    if bullet:
                        current_bullets.append(bullet)
            _flush()
        except Exception:
            pass

    # ── Rule 1b: pair-combine 1-line bullets when long_cv ────────────────────
    if long_cv:
        pair_entries = []
        for section in SECTIONS:
            for entry in (p.get(section) or []):
                desc = entry.get("description")
                if not isinstance(desc, list) or len(desc) < 6:
                    continue
                if all(estimate_bullet_lines(b) == 1 for b in desc):
                    pairs = [(desc[i], desc[i + 1]) for i in range(0, len(desc) - 1, 2)]
                    leftover = desc[len(pairs) * 2] if len(desc) % 2 == 1 else None
                    pair_entries.append(
                        (pairs, leftover, lambda v, e=entry: e.__setitem__("description", v))
                    )

        if pair_entries:
            sections_text = ""
            for ei, (pairs, _, _setter) in enumerate(pair_entries):
                pair_lines = "\n".join(
                    f"PAIR {pi + 1}: {b1} | {b2}"
                    for pi, (b1, b2) in enumerate(pairs)
                )
                sections_text += f"ENTRY {ei + 1}:\n{pair_lines}\n\n"

            prompt = (
                "Each ENTRY below has bullets that each fit on exactly one line. "
                "Combine each PAIR into a single concise bullet of at most 100 characters, "
                "preserving the meaning of both. "
                "Return exactly in this format:\n"
                "ENTRY 1:\nPAIR 1: combined bullet\nPAIR 2: combined bullet\n..."
                "\n\n" + sections_text.strip()
            )
            try:
                resp = await client.responses.create(model="gpt-4o-mini", input=prompt)
                current_entry_idx = None
                current_pairs: list[str] = []

                def _flush_pairs():
                    if current_entry_idx is None:
                        return
                    if 0 <= current_entry_idx < len(pair_entries):
                        pairs_orig, leftover, setter = pair_entries[current_entry_idx]
                        result = list(current_pairs[:len(pairs_orig)])
                        if leftover is not None:
                            result.append(leftover)
                        if result:
                            setter(result)

                for line in resp.output_text.strip().splitlines():
                    line = line.strip()
                    em = re.match(r"^ENTRY\s+(\d+):?$", line, re.IGNORECASE)
                    if em:
                        _flush_pairs()
                        current_entry_idx = int(em.group(1)) - 1
                        current_pairs = []
                    else:
                        pm = re.match(r"^PAIR\s+\d+:\s*(.+)$", line, re.IGNORECASE)
                        if pm and current_entry_idx is not None:
                            current_pairs.append(pm.group(1).strip())
                _flush_pairs()
            except Exception:
                pass

    # ── Rule 2: lengthen widow bullets (up to 3 passes) ──────────────────────
    # A "widow" is a bullet whose last typeset line has exactly 1 word.
    # We add 1 word so that trailing word merges back onto the previous line,
    # reducing the line count by 1 instead of increasing it.
    for _pass in range(3):
        widow_items: list[tuple] = []

        for section in SECTIONS:
            for entry in (p.get(section) or []):
                desc = entry.get("description") or []
                for i, bullet in enumerate(desc):
                    if isinstance(bullet, str) and last_line_word_count(bullet) == 1:
                        widow_items.append(
                            (bullet, lambda v, d=desc, j=i: d.__setitem__(j, v))
                        )

        if not widow_items:
            break

        entries = "\n\n".join(
            f"{i+1}. {text}" for i, (text, _) in enumerate(widow_items)
        )
        prompt = (
            "Each numbered item is a CV bullet point that has exactly 1 word trailing "
            "on a new line when typeset (a widow word). Add the fewest words possible — "
            "ideally just 1 word — at the end of the bullet so the widow word merges "
            "back onto the previous line. The addition must be natural and professional. "
            "Do not change the existing content. "
            "Return one line per item in the same numbered format.\n\n"
            + entries
        )
        try:
            resp = await client.responses.create(model="gpt-4o-mini", input=prompt)
            for num_str, text in re.findall(
                r"^(\d+)\.\s+(.*?)(?=\n\d+\.|\Z)",
                resp.output_text.strip(),
                re.DOTALL | re.MULTILINE,
            ):
                idx = int(num_str) - 1
                if 0 <= idx < len(widow_items):
                    widow_items[idx][1](re.sub(r"\s+", " ", text).strip())
        except Exception:
            break

    return p


async def _compress_bullets_step(profile: dict, client) -> dict:
    """
    Step 2: For sections with >3 bullets, combine/summarise to reduce redundancy.
    """
    import copy
    from openai import AsyncOpenAI

    SECTIONS = ("work_experience", "education", "projects", "extracurriculars")
    p = copy.deepcopy(profile)

    to_combine = []
    for section in SECTIONS:
        for entry in (p.get(section) or []):
            desc = entry.get("description")
            if isinstance(desc, list) and len(desc) > 3:
                to_combine.append(
                    (list(desc), lambda v, e=entry: e.__setitem__("description", v))
                )

    if not to_combine:
        return p

    sections_text = "\n\n".join(
        "ENTRY {}:\n{}".format(i + 1, "\n".join(f"- {b}" for b in bullets))
        for i, (bullets, _) in enumerate(to_combine)
    )
    prompt = (
        "Each ENTRY below has more than 3 CV bullets. Combine and summarise them "
        "to reduce redundancy while keeping all key information. Target 3 bullets "
        "per entry, 2 at minimum. Each bullet must stay under 110 characters. "
        "Use strong action verbs. Do not invent new facts.\n\n"
        "Return in exactly this format:\n"
        "ENTRY 1:\n- bullet\n- bullet\n\nENTRY 2:\n- bullet\n\n"
        + sections_text
    )
    try:
        resp = await client.responses.create(model="gpt-4o-mini", input=prompt)
        current_idx = None
        current_bullets: list[str] = []

        def _flush():
            if current_idx is not None and 0 <= current_idx < len(to_combine):
                if current_bullets:
                    to_combine[current_idx][1](list(current_bullets))

        for line in resp.output_text.strip().splitlines():
            line = line.strip()
            m = re.match(r"^ENTRY\s+(\d+):?$", line, re.IGNORECASE)
            if m:
                _flush()
                current_idx = int(m.group(1)) - 1
                current_bullets = []
            elif line.startswith(("-", "•")) and current_idx is not None:
                bullet = re.sub(r"^[-•]\s*", "", line).strip()
                if bullet:
                    current_bullets.append(bullet)
        _flush()
    except Exception:
        pass

    return p


def _merge_skill_sections(profile: dict) -> dict:
    """
    Step 3: If >1 skill sub-section exists and any has <4 skills, merge all
    into a single flat 'Technical & Soft Skills' list.
    """
    import copy
    p = copy.deepcopy(profile)

    skills = p.get("skills")
    if not isinstance(skills, list):
        return p

    # Skills is a list of dicts: [{category, items: [...]}]
    non_empty = [s for s in skills if isinstance(s.get("items"), list) and s["items"]]
    if len(non_empty) <= 1:
        return p

    has_small = any(len(s["items"]) < 4 for s in non_empty)
    if not has_small:
        return p

    all_items: list[str] = []
    for s in non_empty:
        all_items.extend(s["items"])

    # Deduplicate preserving order
    seen: set = set()
    merged: list[str] = []
    for item in all_items:
        key = item.strip().lower()
        if key not in seen:
            seen.add(key)
            merged.append(item)

    p["skills"] = [{"category": "Technical & Soft Skills", "items": merged}]
    return p


async def build_one_page_cv(enhanced_profile: dict, allow_two_pages: bool = False):
    """
    Full compression pipeline. Returns (latex_source, pdf_bytes, compression_warning).
    compression_warning=True means we exhausted all compression options and the
    content still doesn't fit; the caller should tell the user.
    """
    import copy
    from openai import AsyncOpenAI

    if allow_two_pages or compute_allow_two_pages(enhanced_profile):
        latex = generate_latex_cv(enhanced_profile)
        pdf   = compile_to_pdf(latex)
        return latex, pdf, False

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")) if os.getenv("OPENAI_API_KEY") else None

    long = estimate_cv_overlong(enhanced_profile)
    p = await apply_line_rules(enhanced_profile, long_cv=long)

    def _try_compile(profile_dict, **latex_kwargs):
        latex = generate_latex_cv(profile_dict, **latex_kwargs)
        try:
            pdf_bytes, pages = compile_to_pdf_checked(latex)
            return latex, pdf_bytes, pages
        except RuntimeError:
            raise

    # ── Quick check: fits already? ────────────────────────────────────────────
    latex, pdf, pages = _try_compile(p)
    if pages <= 1:
        return latex, pdf, False

    # ── Step 1: widow fix already done in apply_line_rules ───────────────────
    latex, pdf, pages = _try_compile(p)
    if pages <= 1:
        return latex, pdf, False

    # ── Step 2: combine bullets in sections with >3 bullets ──────────────────
    if client:
        p = await _compress_bullets_step(p, client)
    latex, pdf, pages = _try_compile(p)
    if pages <= 1:
        return latex, pdf, False

    # ── Step 3: merge small skill sub-sections ───────────────────────────────
    p = _merge_skill_sections(p)
    latex, pdf, pages = _try_compile(p)
    if pages <= 1:
        return latex, pdf, False

    # ── Fallback: reduce max_bullets 3→2→1 + compact skills ─────────────────
    for mb in [3, 2, 1]:
        latex, pdf, pages = _try_compile(p, max_bullets_override=mb, compact_skills=True)
        if pages <= 1:
            return latex, pdf, False

    # ── Absolute last resort: strip low-priority sections one by one ─────────
    LOW_PRIORITY = ["extracurriculars", "certifications", "awards", "summary"]
    p_slim = copy.deepcopy(p)
    for section in LOW_PRIORITY:
        if section == "summary":
            if not p_slim.get("summary"):
                continue
            p_slim["summary"] = ""
        else:
            if not p_slim.get(section):
                continue
            p_slim[section] = []
        try:
            latex, pdf, pages = _try_compile(p_slim, max_bullets_override=1, compact_skills=True)
        except RuntimeError:
            break
        if pages <= 1:
            return latex, pdf, False

    # ── Compression warning: return best effort + signal to frontend ──────────
    return latex, pdf, True


@app.post("/enhance-description")
async def enhance_description(payload: dict, user_id: str = Depends(get_current_user_id)):
    """Use AI to condense a long job description into tight CV bullet points."""
    from openai import OpenAI
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    if len(text) > 4000:
        text = text[:4000]
    rate_limit(f"enhance:{user_id}", max_calls=20, window=3600)
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.responses.create(
            model="gpt-4o-mini",
            input=(
                "Improve the following CV work experience description. "
                "Fix grammar, improve clarity, and use strong action verbs where appropriate. "
                "Keep the same number of bullet points and the same approximate length. "
                "Do not add new content or remove existing responsibilities. "
                "Output only the improved bullets, one per line, no dashes or markers.\n\n" + text
            ),
        )
        bullets = response.output_text.strip()
        return {"enhanced": bullets}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Enhancement failed: {exc}")


@app.post("/approve-cv/{job_id}")
def approve_cv(
    job_id: str,
    user_id: str = Depends(get_current_user_id),
):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        if job.status not in ("cv_generated",):
            raise HTTPException(status_code=400, detail=f"Job is not in cv_generated state (current: {job.status})")

        user = db.query(User).filter(User.id == user_id).first()
        if user:
            allowed, reason = check_and_increment_searches(user, db)
            if not allowed:
                raise HTTPException(status_code=402, detail={"detail": "upgrade_required", "limit": "free_search_limit"})

        job.is_cv_approved = "true"
        job.status = "pending_matching"
        db.commit()
        return {"job_id": job_id, "status": "pending_matching"}
    finally:
        db.close()


@app.post("/create-checkout-session")
async def create_checkout_session(payload: dict, user_id: str = Depends(get_current_user_id)):
    interval    = payload.get("interval", "month")
    variant_id  = LS_VARIANT_YEARLY if interval == "year" else LS_VARIANT_MONTHLY
    if not variant_id or not LS_API_KEY:
        raise HTTPException(status_code=503, detail="Payments not configured")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        body = {
            "data": {
                "type": "checkouts",
                "attributes": {
                    "checkout_data": {
                        "email": user.email,
                        "custom": {"user_id": user_id},
                    },
                    "product_options": {
                        "redirect_url": f"{FRONTEND_URL}/app.html?upgrade=success",
                    },
                    "checkout_options": {"button_color": "#4F46E5"},
                },
                "relationships": {
                    "store":   {"data": {"type": "stores",   "id": LS_STORE_ID}},
                    "variant": {"data": {"type": "variants", "id": variant_id}},
                },
            }
        }
        resp = requests.post(
            "https://api.lemonsqueezy.com/v1/checkouts",
            json=body,
            headers=_LS_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        url = resp.json()["data"]["attributes"]["url"]
        return {"url": url}
    finally:
        db.close()


@app.post("/ls-webhook", include_in_schema=False)
async def ls_webhook(request: Request):
    raw     = await request.body()
    sig     = request.headers.get("X-Signature", "")
    digest  = hmac.new(LS_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, sig):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event   = json.loads(raw)
    et      = event.get("meta", {}).get("event_name", "")
    data    = event.get("data", {})
    attrs   = data.get("attributes", {})
    # user_id is passed as custom_data at checkout creation
    user_id = event.get("meta", {}).get("custom_data", {}).get("user_id")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first() if user_id else None

        if et in ("subscription_created", "subscription_updated"):
            if user:
                status = attrs.get("status", "")
                user.stripe_subscription_id = str(data.get("id", ""))
                user.subscription_tier   = "pro" if status == "active" else "free"
                user.subscription_status = status
                db.commit()

        elif et == "subscription_cancelled":
            if user:
                user.subscription_tier   = "free"
                user.subscription_status = "canceled"
                db.commit()

        elif et == "subscription_payment_failed":
            if user:
                user.subscription_status = "past_due"
                db.commit()

    finally:
        db.close()

    return {"received": True}


@app.post("/cancel-subscription")
async def cancel_subscription(user_id: str = Depends(get_current_user_id)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.stripe_subscription_id:
            raise HTTPException(status_code=404, detail="No active subscription found")

        sub_id = user.stripe_subscription_id
        resp = requests.patch(
            f"https://api.lemonsqueezy.com/v1/subscriptions/{sub_id}",
            json={"data": {"type": "subscriptions", "id": sub_id, "attributes": {"cancelled": True}}},
            headers=_LS_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        user.subscription_status = "canceling"
        db.commit()
        return {"status": "canceling"}
    finally:
        db.close()


# ── Fast CV preview (no AI, no queue) ─────────────────────────────────────────

@app.post("/preview-cv")
async def preview_cv(
    payload: dict,
    user_id: str = Depends(get_current_user_id),
    request: Request = None,
):
    rate_limit(f"preview:{user_id}", max_calls=60, window=3600)

    candidate_profile = payload.get("candidate_profile")
    job_id_to_update  = payload.get("job_id")  # present when editing a saved CV
    allow_two_pages   = bool(payload.get("allow_two_pages", False))
    if not candidate_profile:
        raise HTTPException(status_code=400, detail="Missing candidate_profile")

    cleaned_profile  = clean_profile_input(candidate_profile)
    enhanced_profile = await auto_enhance_cv_descriptions(cleaned_profile)

    try:
        latex_source, pdf_bytes, compression_warning = await build_one_page_cv(
            enhanced_profile, allow_two_pages=allow_two_pages
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {exc}")

    if compression_warning:
        return {"compression_warning": True}

    # If editing an existing CV, persist the compiled result so downloads
    # always reflect the latest preview without a separate Save action.
    if job_id_to_update:
        db = SessionLocal()
        try:
            job = db.query(Job).filter(
                Job.id == job_id_to_update, Job.user_id == user_id
            ).first()
            if job:
                if pdf_bytes:
                    pdf_path = f"generated/{job_id_to_update}.pdf"
                    r2.upload_bytes(pdf_path, pdf_bytes, content_type="application/pdf")
                    job.generated_pdf_path = pdf_path
                job.generated_latex    = latex_source
                job.candidate_profile  = json.dumps(candidate_profile)
                db.commit()
        except Exception:
            pass  # preview still works even if the DB write fails
        finally:
            db.close()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=cv-preview.pdf"},
    )
