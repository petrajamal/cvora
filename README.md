# Cvora

An AI-powered CV builder and job matching platform. Users build or upload a CV, receive a professionally typeset single-page PDF, and get a ranked list of live job matches — all in one workflow.

Live at [cvora.live](https://cvora.live) · [www.cvora.live](https://www.cvora.live)

---

## Features

- **CV Upload** — Upload an existing PDF; text is extracted via PyMuPDF and parsed into a structured profile using GPT-4o.
- **CV Builder** — Construct a CV from scratch through a guided form covering work experience, education, skills, projects, and extracurriculars.
- **LaTeX PDF Generation** — CVs are compiled server-side using `pdflatex` into a professionally formatted single-page PDF.
- **One-Page Enforcement** — The system automatically compresses CV content to fit one page for candidates with under 7 years of experience, without manual editing.
- **Live Job Matching** — Fetches live listings from JSearch and Adzuna using job titles from the CV plus GPT-generated synonyms, then scores each job across five dimensions: skills, role relevance, location, experience, and candidate stage.
- **CV Preview** — Users preview the generated PDF in-browser before approving it for job matching.
- **Results History** — All past analyses and matched job results are saved and accessible from the dashboard.
- **Liked Jobs** — Users can bookmark jobs from their results for later reference.
- **User Authentication** — Registration, login, email verification, and password reset via tokenised email links.
- **Subscription Tiers** — Free tier with monthly usage limits; Pro tier with unlimited access.

---

## Architecture

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI (Python), deployed on Railway |
| Database | PostgreSQL |
| Background Worker | Decoupled async worker process on Railway |
| Frontend | Vanilla JS / HTML / CSS, deployed on Cloudflare Pages |
| CV Generation | LaTeX (`pdflatex`) compiled server-side |
| AI | OpenAI GPT-4o (extraction, synonym generation), `text-embedding-3-small` (role similarity) |
| Job APIs | JSearch, Adzuna |
| File Storage | Cloudflare R2 (private buckets) |
| Domain | Namecheap — served at `cvora.live` and `www.cvora.live` via Cloudflare DNS |

---

## How It Works

1. The user uploads a PDF or fills in the CV builder form.
2. The API creates a job record and dispatches it to the background worker.
3. The worker extracts and refines the profile, generates a LaTeX CV, compiles it to PDF, and uploads it to R2.
4. The user previews the PDF and clicks Approve.
5. The worker fetches live job listings, scores each one against the profile, and saves the ranked results.
6. The frontend polls for status every 3 seconds and displays results when ready.

---

## Key Design Decisions

- **Stateless API** — All authentication is carried in JWTs; any number of server instances can handle any request.
- **Async Worker** — CV processing runs in a separate process so the API stays responsive under load.
- **API Caching** — Job API responses are cached in-memory for 2 hours per unique query to reduce cost and latency.
- **Heartbeat Timeout** — Jobs are automatically cancelled if the browser is closed for more than 45 seconds.
- **Graceful Degradation** — If one job API fails, matching continues with results from the other source.
- **Privacy by Design** — No tracking cookies; only a session JWT in `localStorage`; all CV files in private storage.

---

## Project Structure

```
cvora/
├── backend/
│   ├── main.py          # FastAPI application and all API endpoints
│   ├── worker.py        # Background CV processing and job matching
│   ├── latex_gen.py     # LaTeX CV generation and one-page compression
│   └── models.py        # SQLAlchemy database models
└── frontend/
    ├── app.html         # Main application UI
    ├── app.js           # Frontend logic
    ├── index.html       # Landing page
    ├── _headers         # Cloudflare Pages cache headers
    └── _redirects       # Cloudflare Pages redirect rules
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `OPENAI_API_KEY` | OpenAI API key |
| `JSEARCH_API_KEY` | RapidAPI key for JSearch |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | Adzuna API credentials |
| `R2_*` | Cloudflare R2 bucket credentials |
| `JWT_SECRET` | Secret key for JWT signing |
| `SMTP_*` | Email credentials for auth emails |
