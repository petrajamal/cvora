from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Depends, Request
from fastapi.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
import uuid
import os
import json
import re
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict
from datetime import datetime

from latex_gen import generate_latex_cv, compile_to_pdf

from database import Base, engine, SessionLocal
from models import Job, User, PasswordResetToken, LikedJob
from fastapi.middleware.cors import CORSMiddleware
from auth import (
    hash_password, verify_password,
    create_access_token, get_current_user_id,
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

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DEV_MODE = os.getenv("DEV_MODE", "true").lower() == "true"

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1")


def _send_reset_email(to_email: str, raw_token: str):
    if not SMTP_USER or not SMTP_PASS:
        print("[EMAIL] SMTP not configured — skipping email send.")
        return
    reset_link = f"{FRONTEND_URL}?token={raw_token}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Reset your CVora password"
    msg["From"]    = f"CVora <{SMTP_USER}>"
    msg["To"]      = to_email
    html = f"""
    <div style="font-family:Inter,system-ui,sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;color:#0F172A;">
      <div style="margin-bottom:24px;">
        <span style="background:#4F46E5;color:white;padding:6px 12px;border-radius:8px;font-weight:700;font-size:14px;">CVora</span>
      </div>
      <h2 style="font-size:20px;font-weight:700;margin:0 0 8px;">Reset your password</h2>
      <p style="color:#475569;margin:0 0 24px;font-size:14px;">Click the button below to set a new password. This link expires in 60 minutes.</p>
      <a href="{reset_link}" style="display:inline-block;background:#4F46E5;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;">Reset Password</a>
      <p style="color:#94A3B8;margin-top:24px;font-size:12px;">If you didn't request this, you can safely ignore this email.</p>
    </div>
    """
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        print(f"[EMAIL] Reset email sent to {to_email}")
    except Exception as exc:
        print(f"[EMAIL] Failed to send email: {exc}")


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.post("/register")
def register(payload: dict, request: Request):
    rate_limit(f"register:{request.client.host}", max_calls=10, window=300)

    email     = sanitize(payload.get("email") or "", 120).lower()
    password  = sanitize(payload.get("password") or "", 200)
    full_name = sanitize(payload.get("full_name") or "", 100)

    if not full_name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if len(full_name) > 100:
        raise HTTPException(status_code=400, detail="Full name is too long")
    if not email or not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")

    validate_password_strength(password)

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            raise HTTPException(status_code=409, detail="Email already registered")

        user = User(
            id=str(uuid.uuid4()),
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
        )
        db.add(user)
        db.commit()

        token = create_access_token(user.id)
        return {"token": token, "email": user.email, "full_name": user.full_name}
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

        token = create_access_token(user.id)
        return {"token": token, "email": user.email, "full_name": user.full_name}
    finally:
        db.close()


# ── Forgot / reset password ───────────────────────────────────────────────────

@app.post("/forgot-password")
def forgot_password(payload: dict, request: Request):
    """
    Always returns a generic 200 to prevent email-enumeration attacks.
    In DEV_MODE the raw token is included in the response so you can test
    without an SMTP server.  Remove that field before going to production
    and wire up a real email sender instead.
    """
    rate_limit(f"forgot:{request.client.host}", max_calls=5, window=300)

    email = sanitize(payload.get("email") or "", 120).lower()
    generic = {"message": "If that email is registered you will receive a reset link shortly."}

    if not email or not EMAIL_RE.match(email):
        return generic

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
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
    new_password = sanitize(payload.get("password") or "", 200)

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
            "full_name":  user.full_name,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }
    finally:
        db.close()


@app.get("/my-jobs")
def my_jobs(user_id: str = Depends(get_current_user_id)):
    """Return a summary list of this user's past CV builds / job matches."""
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
            # Prefer AI-structured data (upload flow), fall back to builder profile
            raw_profile = (
                json.loads(job.ai_structured_data) if job.ai_structured_data else
                json.loads(job.candidate_profile)  if job.candidate_profile  else {}
            )
            top = matched[0] if matched else None
            result.append({
                "job_id":         job.id,
                "status":         job.status,
                "filename":       job.filename,
                "candidate_name": raw_profile.get("full_name"),
                "match_count":    len(matched),
                "top_match":      {
                    "title":      top.get("title"),
                    "company":    top.get("company"),
                    "match_score": top.get("match_score"),
                } if top else None,
                "has_pdf":        bool(
                    job.generated_pdf_path and
                    os.path.exists(job.generated_pdf_path)
                ),
                "created_at":     job.created_at.isoformat() if job.created_at else None,
            })
        return {"jobs": result}
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

        with open(file_path, "wb") as f:
            f.write(content)

        preferences = {
            "modes": [sanitize(m, 50) for m in modes.split(",") if m.strip()],
            "relocation_locations": [sanitize(loc, 10) for loc in relocation_locations.split(",") if loc.strip()],
        }

        job = Job(
            id=job_id,
            user_id=user_id,
            status="pending",
            filename=safe_name,
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
        if not job.generated_pdf_path or not os.path.exists(job.generated_pdf_path):
            raise HTTPException(status_code=404, detail="PDF not ready yet")
        return FileResponse(
            job.generated_pdf_path,
            media_type="application/pdf",
            filename="cv.pdf",
        )
    finally:
        db.close()


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

    try:
        latex_source = generate_latex_cv(candidate_profile)
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
