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
from collections import defaultdict
from datetime import datetime

from latex_gen import generate_latex_cv, compile_to_pdf

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
FRONTEND_URL   = os.getenv("FRONTEND_URL", "http://127.0.0.1")
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
            "email":      user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
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
        # Delete R2 objects
        for key in [job.file_path, job.generated_pdf_path]:
            if key:
                r2.delete(key)
        db.delete(job)
        db.commit()
        return {"ok": True}
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

    # Validate file type by extension and MIME
    allowed_mime = {"application/pdf", "application/x-pdf"}
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")
    if file.content_type and file.content_type not in allowed_mime:
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    db = SessionLocal()
    try:
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

    enhanced_profile = await auto_enhance_cv_descriptions(candidate_profile)

    try:
        latex_source = generate_latex_cv(enhanced_profile)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LaTeX generation failed: {exc}")

    try:
        pdf_bytes = compile_to_pdf(latex_source)
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
def download_cv_latex(job_id: str, user_id: str = Depends(get_current_user_id)):
    """Download the LaTeX source (.tex) for a built CV."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="Not found")
        if not job.generated_latex:
            raise HTTPException(status_code=404, detail="LaTeX source not available for this CV")
        name = (job.display_name or job.filename or "cv").replace(" ", "_")
        return Response(
            content=job.generated_latex.encode("utf-8"),
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


async def auto_enhance_cv_descriptions(profile: dict) -> dict:
    """Batch-enhance all CV description fields before PDF generation."""
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
        "You are polishing a CV document. For each numbered description, return a cleaned version.\n"
        "Rules (apply in order):\n"
        "1. Fix grammar and spelling always.\n"
        "2. Use strong action verbs.\n"
        "3. If fewer than 10 words: expand slightly using the context — do NOT invent unrelated facts.\n"
        "4. If more than 40 words: condense to approximately 40 words (about 3 printed lines), keeping all key information.\n"
        "5. If already 10–40 words with good grammar: minimal changes only.\n"
        "6. Never change meaning or add facts not stated or strongly implied by the user.\n"
        "Return ONLY the numbered items in the same order, one per line. Format exactly:\n"
        "1. improved text\n2. improved text\n\n"
        + entries
    )

    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = await client.responses.create(model="gpt-4o-mini", input=prompt)
        for line in re.split(r"\n(?=\d+\.)", resp.output_text.strip()):
            m = re.match(r"^(\d+)\.\s+(.*)", line.strip(), re.DOTALL)
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(items):
                    items[idx][2](m.group(2).strip())
    except Exception:
        return profile  # fall back to original on any failure

    return p


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

        job.is_cv_approved = "true"
        job.status = "pending_matching"
        db.commit()
        return {"job_id": job_id, "status": "pending_matching"}
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
    if not candidate_profile:
        raise HTTPException(status_code=400, detail="Missing candidate_profile")

    enhanced_profile = await auto_enhance_cv_descriptions(candidate_profile)

    try:
        latex_source = generate_latex_cv(enhanced_profile)
        pdf_bytes = compile_to_pdf(latex_source)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {exc}")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=cv-preview.pdf"},
    )
