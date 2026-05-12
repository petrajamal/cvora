# CVora — Project Handoff

## 1. Goal

Build **CVora** — a web app where users:
1. Build a professional CV using a guided form builder (or upload an existing PDF)
2. Get a clean, typeset PDF generated from LaTeX automatically
3. Receive a curated list of job listings matched to their CV profile
4. Save jobs they like to their dashboard

The app is a capstone project. The technical ambition is: AI-assisted CV formatting (GPT-4o-mini for enhancement + compression), real LaTeX PDF generation via pdflatex, and live job search matching.

---

## 2. Current State of the Project

### Infrastructure
- **Backend**: FastAPI on Railway (Python). PostgreSQL DB. Cloudflare R2 for file storage (PDFs, LaTeX source).
- **Frontend**: Vanilla JS + HTML/CSS on Cloudflare Pages.
- **AI**: OpenAI GPT-4o-mini for CV bullet enhancement and line-rule compression.
- **PDF generation**: `pdflatex` runs on the Railway server inside a Docker container.

### What works end-to-end
- User registration, login, email verification, password reset (JWT auth)
- CV Builder: full guided form (personal info, education, work experience, projects, extracurriculars, certifications, awards, languages, skills, links)
- CV Upload: PDF → PyMuPDF text extraction → GPT structured JSON parsing
- LaTeX CV generation from profile JSON → pdflatex → PDF stored in R2
- Preview CV (iframe) + Download PDF/LaTeX
- Job matching: GPT reads CV profile + user preferences → search query → scrape live job listings → score + rank
- Liked jobs: save/unlike from results page, persisted per user
- Multiple CVs per account (dashboard), rename/delete
- Section renaming (custom labels for Education, Experience, etc.)
- Per-entry hide/show toggle (eye icon): hides entries from the PDF without deleting them
- Per-skill-group and per-language hide toggle
- 1-page hard enforcement for < 7 years experience:
  - Iterates max_bullets 3→2→1
  - compact_skills: flat skills line (no labels) when fitting 1 page
  - Strips low-priority sections (extracurriculars → certifications → awards → summary) if still 2 pages
- Line-rule compression system (runs before each compile):
  - Rule 1: GPT summarises entries with > 6 estimated lines
  - Rule 1b: pair-combines 1-line bullets (6+ bullets/entry) when CV is long
  - Rule 2: widow-word fix — GPT shortens bullets where 1-2 words trail alone on a new line
- Auto-save on preview: every `/preview-cv` call with a job_id saves PDF + LaTeX + profile to DB so downloads always reflect the latest preview
- Landing page: light design, product preview mock (form panel + LaTeX CV panel), features, how it works, CTA

### Known working quirks / calibrations
- `_BULLET_CPL = 100`: chars per line threshold for bullet width estimation (calibrated against real pdflatex output)
- `estimate_cv_overlong` threshold: 52 lines triggers long_cv compression
- `_needs_enhancement(text)`: gates AI enhancement to < 6-word bullets or junk/placeholder text only — prevents hallucination on normal text
- `splitBullets()` in frontend: merges continuation lines when pasted text has soft wraps

---

## 3. What Changed This Session

### `backend/main.py`
- **Hard 1-page rule for < 7 years**: after mb=1 still gives 2 pages, now strips low-priority sections one by one (extracurriculars → certifications → awards → summary) and recompiles each time
- `build_one_page_cv`: passes `compact_skills=True` in the iterative bullet-reduction loop

### `backend/latex_gen.py`
- `generate_latex_cv(profile, max_bullets_override, compact_skills)`: added `compact_skills` param — when True and all visible skills fit ≤ 100 chars, outputs flat comma-separated skills (no Technical/Tools/Soft labels), saving ~1 line

### `frontend/app.html`
- Entry card CSS redesign: header row puts title + eye/remove buttons on one line with a border-bottom separator
- `h4` in entry cards: uppercase, 10.5px, letter-spaced (matches landing page mock style)
- Labels inside entry cards: uppercase, 10.5px, `#9CA3AF` gray
- Inputs inside entry cards: slightly compact (8px padding, `#F9FAFB` background)
- Added `input:not([type])` and `input[type="number"]` to the global form input CSS selector (previously these didn't match `input[type="text"]` and showed as raw browser inputs)
- Mobile `#authScreen`: `width: calc(100% - 40px)` so login card doesn't touch screen edges on phones

### `frontend/app.js`
- `createEntryCard()`: restructured to `<entry-card-header>` containing `<h4>` + `<div.inline-actions>` on one row
- Added `_EYE_OPEN` / `_EYE_OFF` SVG constants, `_applyVisHidden()` helper
- Eye toggle: purple border/icon when visible, gray eye-off icon when hidden, opacity 0.45 on card
- Section-level hide toggles for Technical Skills, Tools, Soft Skills, Languages

### `frontend/index.html` (landing page)
- Full redesign: dark → light (`#FAFAFA`), removed AI-template tells (purple glow, gradient text, font-weight 800, buzzword copy)
- Added product preview section: browser-chrome frame with split form panel (left) and LaTeX CV panel (right)
- CV panel: Georgia serif font, open-circle bullets `◦`, inline SVG icons in contact line (pin, envelope, phone, LinkedIn, GitHub) — matches actual pdflatex FontAwesome output
- Mobile: CV panel hidden on ≤ 700px

---

## 4. Failed Attempts / Dead Ends

- **6-line bullet cap by dropping**: originally dropped bullets beyond line 6. User's 6 real bullets cover ~10 lines so the last 2 were dropped. Reverted to GPT summarisation instead.
- **AI hallucinating extra bullets**: the enhancement prompt initially had no guard — it expanded short entries into long bullet lists. Fixed by `_needs_enhancement()` gate.
- **Pair-combining making things worse**: Rule 1b combines pairs of 1-line bullets into longer single bullets. This can make the CV longer than the original if the combined line wraps. The heuristic sometimes mis-classifies a CV as "long" when it's borderline, triggering pairing unnecessarily. No clean fix found — accepted as a known rough edge.
- **Double compile on Save**: after saving a CV, `updateCvPreview()` was called immediately, causing a second pdflatex compile. Removed the double call; now fetches the stored PDF from R2 directly.
- **Reliable line-count estimation from LaTeX source**: tried to parse LaTeX source to count lines accurately. Not reliable — too many edge cases (kerning, font metrics, hyphenation). Settled on the 100-chars/line heuristic for bullet content only.

---

## 5. Project Files

```
cv-job-matcher/
├── backend/
│   ├── main.py          (1473 lines) — FastAPI app, all endpoints, CV pipeline logic
│   ├── latex_gen.py     ( 747 lines) — LaTeX source generation, page estimation, compression
│   ├── models.py        (  69 lines) — SQLAlchemy models: User, Job, LikedJob, PasswordResetToken
│   ├── database.py                   — SQLAlchemy engine + SessionLocal setup
│   ├── auth.py                       — JWT helpers, password hashing
│   ├── r2.py                         — Cloudflare R2 upload/download helpers
│   ├── requirements.txt              — fastapi, uvicorn, sqlalchemy, openai, PyMuPDF, boto3, psycopg2
│   └── .env                          — SECRET_KEY, DATABASE_URL, OPENAI_API_KEY, R2 credentials
├── frontend/
│   ├── app.html         (2170 lines) — Main app: all CSS + HTML shell
│   ├── app.js           (2716 lines) — All frontend logic: builder, upload, preview, job results
│   └── index.html       (1011 lines) — Public landing page
├── Dockerfile                        — Railway container: installs texlive-latex-base + app
└── handoff.md                        — This file
```

### Key backend endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/register` | Create account |
| POST | `/login` | JWT login |
| POST | `/preview-cv` | Compile LaTeX preview, auto-save if job_id provided |
| POST | `/save-cv-only` | Save profile + name without recompiling |
| POST | `/upload-cv` | PDF upload → text extract → GPT parse → job record |
| POST | `/match-jobs` | GPT job search + scoring |
| GET | `/view-cv/{job_id}` | Return stored PDF from R2 |
| GET | `/download-latex/{job_id}` | Return stored .tex source |
| GET | `/jobs` | List user's CV jobs |
| DELETE | `/delete-job/{job_id}` | Delete job + cascade LikedJob |
| GET/POST/DELETE | `/liked-jobs` | Save / list / unlike jobs |

### Key frontend functions (app.js)
| Function | Purpose |
|----------|---------|
| `createEntryCard(title, html)` | Renders a builder entry card with header row |
| `buildCandidateProfile()` | Collects all form data → profile JSON |
| `updateCvPreview()` | POST to /preview-cv, refreshes iframe |
| `editBuilderCv(jobId)` | Loads existing CV into builder for editing |
| `splitBullets(text)` | Splits textarea into bullet array, merging soft-wrap continuations |
| `_applyVisHidden(btn, card, hidden)` | Toggles eye icon + opacity on entry cards |
| `build_one_page_cv(profile)` | Backend: iterative 1-page enforcement |
| `apply_line_rules(profile, long_cv)` | Backend: Rule 1/1b/2 compression |

---

## 6. Next Steps / What Remains

### High priority
1. **Job matching quality**: the GPT search query generation + scraping is the weakest part. Results can be irrelevant or stale. Consider switching to a real job board API (Adzuna, RapidAPI jobs, etc.) instead of scraping.
2. **CV download always current**: verify that every download path (LaTeX + PDF) returns the version from the last preview, not an older compile. The auto-save-on-preview logic should handle this but worth end-to-end testing.
3. **Builder UX — entry counter badge**: the landing page mock shows "ENTRY 1 OF 2" in the card header. This is not implemented in the real app — easy win, adds clarity.

### Medium priority
4. **Mobile builder**: the builder form is functional on mobile but not optimised. The preview panel stacks below the form; the split layout is tricky on small screens.
5. **Upload CV parsing accuracy**: PyMuPDF + GPT sometimes mispopulates fields (wrong dates, merged entries). A review/edit step post-parse would help.
6. **Password change from settings**: forgot-password flow exists but there's no in-app "change password" for logged-in users.
7. **Email verification UX**: the verify-email page works but the flow after clicking the link is abrupt. Could redirect directly into the app.

### Low priority / Polish
8. **Landing page**: currently has a product preview mock. A real screenshot or screen recording would be more convincing.
9. **Cloudflare Web Analytics**: metrics were showing 0 — make sure the analytics script tag is present and the zone is configured in the Cloudflare dashboard.
10. **Capstone report**: the user is writing a capstone report. The full session transcript is at `/Users/petrajamal/.claude/projects/-Users-petrajamal-cv-job-matcher/d27daef4-97f0-4e07-ac74-58d296713095.jsonl` — useful for documenting architecture decisions and iterations.
