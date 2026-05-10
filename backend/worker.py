import time
import hashlib
import fitz
import re
import json
import os
import shutil
import subprocess
import traceback
import requests
import html as html_module

from database import SessionLocal
from models import Job
from dotenv import load_dotenv
import r2
from openai import OpenAI
from datetime import datetime

# TeX constants and LaTeX generation helpers live in latex_gen.py so that
# main.py's /preview-cv endpoint can use them without importing the whole worker.
from latex_gen import (
    PDFLATEX_BIN, _TEX_ENV,
    latex_escape, render_bullets, ensure_list,
    generate_latex_cv, compile_to_pdf,
)

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ADZUNA_APP_ID  = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
JSEARCH_API_KEY = os.getenv("JSEARCH_API_KEY", "")

import hashlib as _hashlib
_ai_extract_cache: dict = {}
_ai_refine_cache: dict = {}

# ─────────────────────────────────────────────
# ADZUNA: known skills vocabulary
# ─────────────────────────────────────────────
KNOWN_TECH_SKILLS = {
    # Technical
    "python", "java", "javascript", "typescript", "c++", "c#", "ruby",
    "go", "golang", "rust", "php", "scala", "kotlin", "swift",
    "r", "matlab", "perl", "bash", "shell", "powershell", "vba",
    "html", "css", "sass", "tailwind", "bootstrap", "api",
    "react", "vue", "angular", "next.js", "nuxt", "svelte",
    "node.js", "express", "django", "flask", "fastapi", "spring",
    "laravel", "rails", "asp.net", "graphql", "rest", "grpc", "soap",
    "sql", "mysql", "postgresql", "mongodb", "sqlite", "redis",
    "elasticsearch", "cassandra", "dynamodb", "oracle", "firebase",
    "supabase", "mariadb",
    "docker", "kubernetes", "aws", "azure", "gcp", "git", "linux",
    "terraform", "ansible", "jenkins", "ci/cd", "github actions",
    "gitlab ci", "nginx", "apache", "serverless",
    "tensorflow", "pytorch", "scikit-learn", "pandas", "numpy",
    "matplotlib", "keras", "hugging face", "spark", "hadoop",
    "kafka", "airflow", "databricks", "dbt",
    "tableau", "power bi", "excel", "google sheets",
    "machine learning", "deep learning", "nlp", "computer vision",
    "data science", "data engineering", "llm", "ai",
    "flutter", "react native", "android", "ios",
    "selenium", "cypress", "jest", "pytest", "junit", "playwright",
    "figma", "photoshop", "illustrator", "indesign", "after effects",
    "premiere", "sketch", "canva",
    "n8n", "zapier", "power automate", "make", "automation",
    "salesforce", "hubspot", "sap", "erp", "crm",
    "networking", "tcp/ip", "cybersecurity", "penetration testing",
    "firewalls", "vpn", "ccna",
    "autocad", "solidworks", "revit", "catia", "ansys",
    "blockchain", "solidity", "web3", "unity", "unreal engine",
    "microservices", "agile", "scrum", "jira", "confluence",
    "wordpress", "shopify", "woocommerce",
    # Business
    "process improvement", "business analysis", "business analyst",
    "lean", "six sigma", "lean six sigma", "dmaic", "kaizen", "5s",
    "project management", "pmp", "prince2", "change management",
    "stakeholder management", "requirements gathering", "gap analysis",
    "root cause analysis", "kpi", "okr", "reporting", "dashboards",
    "operations management", "supply chain", "logistics", "procurement",
    "quality management", "iso", "iso 9001", "quality assurance",
    "risk management", "compliance", "audit", "governance",
    "business intelligence", "data analysis", "data visualization",
    "financial analysis", "budgeting", "forecasting", "accounting",
    "bookkeeping", "taxation", "accounts payable",
    "accounts receivable", "financial modeling",
    "marketing", "digital marketing", "seo", "sem",
    "google analytics", "content management", "social media",
    "brand management", "email marketing", "copywriting",
    "sales", "b2b sales", "b2c sales", "lead generation",
    "cold calling", "negotiation", "account management",
    "customer success", "customer service", "client management",
    "vendor management", "contract management",
    "product management", "roadmapping", "product discovery",
    "user stories", "a/b testing", "market research",
    "strategy", "strategic planning",
    # Soft skills
    "communication", "presentation", "leadership", "teamwork",
    "problem solving", "critical thinking", "time management",
    "adaptability", "creativity", "decision making",
    "multitasking", "organization", "attention to detail",
    "emotional intelligence", "conflict resolution",
    "public speaking", "mentoring", "coaching",
    "analytical thinking", "collaboration",
    "work ethic", "self management", "interpersonal skills",
    "active listening", "resilience",
    "accountability", "initiative", "innovation",
    # Industries
    "finance", "banking", "healthcare", "retail",
    "ecommerce", "hospitality", "manufacturing",
    "education", "insurance", "real estate",
    "construction", "telecommunications",
    "transportation", "energy", "oil and gas",
    "government", "nonprofit", "pharmaceutical",
    "media", "entertainment", "automotive",
    "aviation", "food and beverage",
    "saas", "fintech", "edtech", "healthtech", "proptech",
    # HR
    "human resources", "recruitment", "talent acquisition",
    "talent management", "employee relations",
    "performance management", "payroll",
    "onboarding", "offboarding",
    "workforce planning", "benefits administration",
    "hris", "training and development",
    "organizational development",
    # Healthcare
    "patient care", "ehr", "emr", "epic systems",
    "medical coding", "hipaa", "clinical research",
    "medical terminology", "healthcare administration",
    "electronic medical records",
    # Legal / Compliance
    "gdpr", "soc2", "iso 27001",
    "legal research", "regulatory compliance",
    "policy development", "contract review",
    "risk assessment", "aml", "kyc",
    # Design / UX
    "user research", "wireframing",
    "prototyping", "design systems",
    "usability testing", "interaction design",
    "responsive design", "information architecture",
    "visual design", "accessibility",
    # Engineering
    "plc", "scada", "lean manufacturing",
    "quality control", "mechanical design",
    "electrical systems", "cad", "cam", "fea", "hvac",
}

# Locations that aren't natively supported by Adzuna.
# We still map them to "gb" for a broad search, but we must NOT pass
# their city/country as a `where` filter — it would return 0 results.
ADZUNA_UNSUPPORTED_LOCATIONS = {
    "lebanon", "beirut",
    "uae", "dubai", "abu dhabi",
    "saudi arabia", "riyadh", "jeddah",
    "qatar", "doha",
    "egypt", "cairo",
    "jordan", "amman",
    "kuwait", "bahrain", "oman", "muscat",
    "iraq", "baghdad",
    "iran", "tehran",
    "pakistan", "karachi", "lahore",
    "bangladesh", "dhaka",
    "nigeria", "lagos",
    "kenya", "nairobi",
    "ghana", "accra",
}

# Maps location strings → Adzuna country codes
# Countries not in Adzuna default to "gb" (largest job pool)
ADZUNA_COUNTRY_MAP = {
    "uk": "gb", "united kingdom": "gb", "england": "gb",
    "london": "gb", "manchester": "gb", "birmingham": "gb",
    "leeds": "gb", "glasgow": "gb", "edinburgh": "gb",
    "us": "us", "usa": "us", "united states": "us", "america": "us",
    "new york": "us", "san francisco": "us", "los angeles": "us",
    "chicago": "us", "boston": "us", "seattle": "us", "austin": "us",
    "canada": "ca", "toronto": "ca", "vancouver": "ca", "montreal": "ca",
    "australia": "au", "sydney": "au", "melbourne": "au", "brisbane": "au",
    "germany": "de", "berlin": "de", "munich": "de", "hamburg": "de",
    "france": "fr", "paris": "fr", "lyon": "fr",
    "india": "in", "bangalore": "in", "mumbai": "in",
    "delhi": "in", "hyderabad": "in", "pune": "in",
    "netherlands": "nl", "amsterdam": "nl",
    "new zealand": "nz", "auckland": "nz",
    "poland": "pl", "warsaw": "pl", "krakow": "pl",
    "singapore": "sg",
    "south africa": "za", "cape town": "za", "johannesburg": "za",
    "brazil": "br", "sao paulo": "br",
    "italy": "it", "rome": "it", "milan": "it",
    "spain": "es", "madrid": "es", "barcelona": "es",
    "belgium": "be", "brussels": "be",
    "switzerland": "ch", "zurich": "ch",
    "austria": "at", "vienna": "at",
    "mexico": "mx",
    # MENA — not directly supported by Adzuna, route to gb
    "lebanon": "gb", "beirut": "gb",
    "uae": "gb", "dubai": "gb", "abu dhabi": "gb",
    "saudi arabia": "gb", "riyadh": "gb",
    "qatar": "gb", "doha": "gb",
    "egypt": "gb", "cairo": "gb",
    "jordan": "gb", "amman": "gb",
    "kuwait": "gb", "bahrain": "gb",
}

# ─────────────────────────────────────────────
# ADZUNA: helper functions
# ─────────────────────────────────────────────

def clean_html(text):
    """Strip HTML tags and decode entities from Adzuna descriptions."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_skills_from_text(text):
    """Return known tech skills found in a job description."""
    text_lower = text.lower()
    found = []
    for skill in KNOWN_TECH_SKILLS:
        if re.search(r"\b" + re.escape(skill) + r"\b", text_lower):
            found.append(skill)
    return found


def infer_years_from_text(text):
    """Return minimum years of experience mentioned in a job description."""
    text_lower = text.lower()
    patterns = [
        r"(\d+)\s*\+\s*years?\s*(?:of\s+)?(?:experience|exp)",
        r"(\d+)\s*-\s*\d+\s*years?\s*(?:of\s+)?(?:experience|exp)",
        r"minimum\s+(\d+)\s*years?",
        r"at\s+least\s+(\d+)\s*years?",
        r"(\d+)\s*years?\s*(?:of\s+)?(?:experience|exp)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            return int(match.group(1))
    return 0


def infer_target_candidates_from_text(text):
    """Infer appropriate candidate stages from job description keywords."""
    t = text.lower()
    if any(k in t for k in ["internship", "intern position", "intern role", "student intern"]):
        return ["student", "fresh_grad", "intern"]
    if any(k in t for k in ["entry level", "entry-level", "recent graduate",
                             "fresh graduate", "0-1 year", "no experience required"]):
        return ["fresh_grad", "junior"]
    if any(k in t for k in ["senior", "lead ", "principal", "staff engineer",
                             "7+ years", "8+ years", "10+ years"]):
        return ["senior", "mid_level"]
    if any(k in t for k in ["mid level", "mid-level", "3-5 years", "4-6 years", "5+ years"]):
        return ["mid_level", "junior"]
    if any(k in t for k in ["junior", "1-2 years", "1-3 years", "2+ years"]):
        return ["junior", "fresh_grad"]
    return ["fresh_grad", "junior", "mid_level"]


def extract_role_keywords(title, description):
    """Build role_keywords list from job title words + top skills in description."""
    stop = {"a", "an", "the", "and", "or", "for", "to", "of", "in",
            "at", "on", "with", "&", "is", "are", "we", "you", "our"}
    title_words = [w.lower().strip("()[],-") for w in title.split()
                   if w.lower() not in stop and len(w) > 2]
    desc_skills  = extract_skills_from_text(description)[:8]
    combined = title_words + desc_skills
    seen, result = set(), []
    for kw in combined:
        if kw and kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result


def detect_country(location_str):
    """Map a location string to an Adzuna country code (default: gb)."""
    if not location_str:
        return "gb"
    loc = location_str.lower()
    for key, code in ADZUNA_COUNTRY_MAP.items():
        if key in loc:
            return code
    return "gb"


def build_search_query(ai_data):
    """Derive a job search query string from the candidate's profile."""
    # Most recent position is the strongest signal
    work_experience = ai_data.get("work_experience") or []
    if work_experience:
        pos = (work_experience[-1].get("position") or "").strip()
        if pos:
            return pos

    # Target fields (builder flow)
    setup = ai_data.get("setup") or {}
    target_fields = setup.get("target_fields") or []
    if target_fields:
        return target_fields[0]

    # Fall back to top skills
    skills = ai_data.get("skills") or []
    if skills:
        return " ".join(skills[:3])

    return "software developer"


def map_adzuna_job(raw):
    """Convert one Adzuna API result into our internal job schema."""
    title       = (raw.get("title") or "").strip()
    company     = ((raw.get("company") or {}).get("display_name") or "Unknown Company").strip()
    location    = ((raw.get("location") or {}).get("display_name") or "").strip()
    description = clean_html(raw.get("description") or "")
    url         = raw.get("redirect_url") or ""

    t = (title + " " + location + " " + description).lower()
    if "remote" in t or "work from home" in t or "wfh" in t:
        location_type = "remote"
    elif "hybrid" in t:
        location_type = "hybrid"
    else:
        location_type = "onsite"

    return {
        "title":                    title,
        "company":                  company,
        "location":                 location,
        "location_type":            location_type,
        "description":              description[:600],
        "required_skills":          extract_skills_from_text(description),
        "required_years_experience": infer_years_from_text(description),
        "target_candidates":        infer_target_candidates_from_text(description),
        "role_keywords":            extract_role_keywords(title, description),
        "url":                      url,
    }


def fetch_jobs_from_adzuna(ai_data, preferences):
    """
    Query Adzuna for live job listings based on the candidate profile.
    Returns empty list if credentials are missing or the request fails.
    """
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        print("[JOBS] Adzuna credentials not set — returning empty list")
        return []

    what = build_search_query(ai_data)

    modes               = set(preferences.get("modes", []))
    candidate_location  = ai_data.get("location") or ""
    relocation_targets  = preferences.get("relocation_locations") or []

    where   = ""
    country = "gb"

    def _is_unsupported(loc_str):
        loc_lower = (loc_str or "").lower()
        return any(u in loc_lower for u in ADZUNA_UNSUPPORTED_LOCATIONS)

    if "cv_location" in modes and candidate_location:
        country = detect_country(candidate_location)
        where   = "" if _is_unsupported(candidate_location) else candidate_location
    elif "willing_to_relocate" in modes and relocation_targets:
        country = detect_country(relocation_targets[0])
        where   = "" if _is_unsupported(relocation_targets[0]) else relocation_targets[0]
    elif candidate_location:
        country = detect_country(candidate_location)
        where   = "" if _is_unsupported(candidate_location) else candidate_location

    # Append "remote" to the query when remote mode is selected
    if "remote" in modes:
        what  = what + " remote"
        where = ""          # no location filter for remote

    params = {
        "app_id":           ADZUNA_APP_ID,
        "app_key":          ADZUNA_APP_KEY,
        "results_per_page": 20,
        "what":             what,
        "content-type":     "application/json",
    }
    if where:
        params["where"] = where

    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    print(f"\n[JOBS] Adzuna query country={country}  what='{what}'  where='{where}'")

    try:
        resp = requests.get(url, params=params, timeout=12)
        resp.raise_for_status()
        raw_jobs = resp.json().get("results", [])
        print(f"[JOBS] Adzuna returned {len(raw_jobs)} jobs")
        mapped = [map_adzuna_job(j) for j in raw_jobs]
        return mapped if mapped else []
    except Exception as exc:
        print(f"[JOBS] Adzuna request failed: {exc} — returning empty list")
        return []


# ── Adzuna response cache (in-memory, 1-hour TTL) ────────────────────────────
_adzuna_cache: dict = {}
_CACHE_TTL = 3600  # seconds

def fetch_jobs_cached(ai_data: dict, preferences: dict) -> list:
    key_data = {
        "skills": sorted(ai_data.get("skills", [])),
        "location": ai_data.get("location", ""),
        "prefs": json.dumps(preferences, sort_keys=True),
    }
    cache_key = hashlib.md5(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
    now = time.monotonic()
    if cache_key in _adzuna_cache:
        result, ts = _adzuna_cache[cache_key]
        if now - ts < _CACHE_TTL:
            print(f"[JOBS] Adzuna cache hit ({int(now - ts)}s old)")
            return result
    result = fetch_jobs_from_adzuna(ai_data, preferences)
    _adzuna_cache[cache_key] = (result, now)
    return result


# ─────────────────────────────────────────────
# JSEARCH: primary job source (covers MENA via Google for Jobs)
# ─────────────────────────────────────────────

# Maps ISO country codes → location keywords found in job listings.
# Used to score location match when user selects relocation countries.
COUNTRY_CODE_LOCATION_KEYWORDS = {
    "GB": ["united kingdom", "uk", "england", "london", "manchester", "birmingham",
           "edinburgh", "glasgow", "bristol", "leeds", "liverpool"],
    "US": ["united states", "usa", "us", "new york", "los angeles", "san francisco",
           "chicago", "boston", "seattle", "austin", "miami", "denver"],
    "AE": ["united arab emirates", "uae", "dubai", "abu dhabi", "sharjah"],
    "SA": ["saudi arabia", "ksa", "riyadh", "jeddah", "dammam"],
    "QA": ["qatar", "doha"],
    "KW": ["kuwait", "kuwait city"],
    "BH": ["bahrain", "manama"],
    "OM": ["oman", "muscat"],
    "JO": ["jordan", "amman"],
    "LB": ["lebanon", "beirut"],
    "EG": ["egypt", "cairo", "alexandria"],
    "MA": ["morocco", "casablanca", "rabat"],
    "DE": ["germany", "berlin", "munich", "hamburg", "frankfurt", "cologne"],
    "FR": ["france", "paris", "lyon", "marseille"],
    "NL": ["netherlands", "amsterdam", "rotterdam", "the hague"],
    "CH": ["switzerland", "zurich", "geneva", "bern"],
    "SE": ["sweden", "stockholm", "gothenburg"],
    "NO": ["norway", "oslo"],
    "DK": ["denmark", "copenhagen"],
    "FI": ["finland", "helsinki"],
    "BE": ["belgium", "brussels", "antwerp"],
    "AT": ["austria", "vienna"],
    "IE": ["ireland", "dublin"],
    "ES": ["spain", "madrid", "barcelona"],
    "IT": ["italy", "rome", "milan"],
    "PT": ["portugal", "lisbon", "porto"],
    "PL": ["poland", "warsaw", "krakow"],
    "CZ": ["czech republic", "prague"],
    "TR": ["turkey", "istanbul", "ankara"],
    "SG": ["singapore"],
    "IN": ["india", "bangalore", "mumbai", "delhi", "hyderabad", "pune"],
    "JP": ["japan", "tokyo", "osaka"],
    "KR": ["south korea", "korea", "seoul"],
    "AU": ["australia", "sydney", "melbourne", "brisbane", "perth"],
    "NZ": ["new zealand", "auckland", "wellington"],
    "CA": ["canada", "toronto", "vancouver", "montreal", "calgary"],
    "BR": ["brazil", "sao paulo", "rio de janeiro"],
    "MX": ["mexico", "mexico city"],
    "ZA": ["south africa", "johannesburg", "cape town", "durban"],
    "MY": ["malaysia", "kuala lumpur"],
    "HK": ["hong kong"],
    "TH": ["thailand", "bangkok"],
    "PH": ["philippines", "manila"],
    "ID": ["indonesia", "jakarta"],
    "LU": ["luxembourg"],
}

# ISO 3166-1 alpha-2 country codes for jsearch's `country` param
JSEARCH_COUNTRY_CODES = {
    "lebanon": "LB", "beirut": "LB",
    "uae": "AE", "dubai": "AE", "abu dhabi": "AE", "sharjah": "AE",
    "saudi arabia": "SA", "riyadh": "SA", "jeddah": "SA",
    "qatar": "QA", "doha": "QA",
    "egypt": "EG", "cairo": "EG",
    "jordan": "JO", "amman": "JO",
    "kuwait": "KW",
    "bahrain": "BH",
    "oman": "OM", "muscat": "OM",
    "iraq": "IQ", "baghdad": "IQ",
    "morocco": "MA", "casablanca": "MA",
    "uk": "GB", "united kingdom": "GB", "england": "GB",
    "london": "GB", "manchester": "GB",
    "us": "US", "usa": "US", "united states": "US",
    "new york": "US", "san francisco": "US",
    "canada": "CA", "toronto": "CA", "vancouver": "CA",
    "australia": "AU", "sydney": "AU", "melbourne": "AU",
    "germany": "DE", "berlin": "DE",
    "france": "FR", "paris": "FR",
    "india": "IN", "bangalore": "IN", "mumbai": "IN",
    "netherlands": "NL", "amsterdam": "NL",
    "singapore": "SG",
    "south africa": "ZA",
}

def detect_jsearch_country(location_str):
    """Return ISO country code for jsearch, or None if unknown."""
    if not location_str:
        return None
    loc = location_str.lower()
    for key, code in JSEARCH_COUNTRY_CODES.items():
        if key in loc:
            return code
    return None

# Common abbreviations people type in the "target fields" box
_FIELD_EXPANSIONS = {
    "it":  "information technology",
    "hr":  "human resources",
    "ba":  "business administration",
    "cs":  "computer science",
    "mkt": "marketing",
    "fin": "finance",
    "ops": "operations",
    "pm":  "project management",
    "pr":  "public relations",
    "qa":  "quality assurance",
    "ux":  "ux design",
    "ui":  "ui design",
    "ml":  "machine learning",
    "ai":  "artificial intelligence",
    "bd":  "business development",
    "eng": "engineering",
}

def build_jsearch_query(ai_data, preferences):
    """
    Return (query_string, country_code) for jsearch.
    Location is handled via the `country` API parameter, NOT embedded
    in the free-text query — that gave 0 results for MENA queries.
    """
    modes = set(preferences.get("modes", []))

    # --- job title / role part ---
    work_experience = ai_data.get("work_experience") or []
    title_part = ""
    if work_experience:
        title_part = (work_experience[-1].get("position") or "").strip()

    if not title_part:
        setup = ai_data.get("setup") or {}
        fields = setup.get("target_fields") or []
        if fields:
            # Expand known abbreviations (e.g. "BA" → "business administration")
            raw = fields[0].strip()
            title_part = _FIELD_EXPANSIONS.get(raw.lower(), raw)

    if not title_part:
        skills = ai_data.get("skills") or []
        title_part = " ".join(skills[:3]) if skills else "software developer"

    # If query is still suspiciously short, enrich with application level
    if len(title_part) <= 3:
        level_map = {"internship": "intern", "entry": "entry level", "experienced": ""}
        level = ((ai_data.get("setup") or {}).get("application_level") or "")
        level_str = level_map.get(level, "")
        if level_str:
            title_part = f"{title_part} {level_str}".strip()

    # --- country code ---
    candidate_location = (ai_data.get("location") or "").strip()
    relocation_targets = preferences.get("relocation_locations") or []
    country_code = None

    if "remote" in modes:
        return title_part, None

    if "willing_to_relocate" in modes and relocation_targets:
        # relocation_targets are now ISO codes e.g. "GB" — use directly (lowercase for jsearch)
        return title_part, relocation_targets[0].lower()

    if "cv_location" in modes and candidate_location:
        country_code = detect_jsearch_country(candidate_location)
        return title_part, country_code

    if candidate_location:
        country_code = detect_jsearch_country(candidate_location)
        return title_part, country_code

    return title_part, None


def map_jsearch_job(raw):
    """Convert one jsearch result into our internal job schema."""
    title   = (raw.get("job_title") or "").strip()
    company = (raw.get("employer_name") or "Unknown Company").strip()
    url     = raw.get("job_apply_link") or raw.get("job_google_link") or ""

    # Build a readable location string
    city    = raw.get("job_city") or ""
    state   = raw.get("job_state") or ""
    country = raw.get("job_country") or ""
    location_parts = [p for p in [city, state, country] if p]
    location = ", ".join(location_parts)

    # Location type
    if raw.get("job_is_remote"):
        location_type = "remote"
    else:
        description_lower = (raw.get("job_description") or "").lower()
        title_lower       = title.lower()
        if "remote" in title_lower or "work from home" in description_lower or "wfh" in description_lower:
            location_type = "remote"
        elif "hybrid" in title_lower or "hybrid" in description_lower:
            location_type = "hybrid"
        else:
            location_type = "onsite"

    description = (raw.get("job_description") or "").strip()

    # --- required_skills ---
    # jsearch sometimes provides job_required_skills directly
    api_skills = raw.get("job_required_skills") or []
    if api_skills:
        required_skills = [normalize_text(s) for s in api_skills if s]
    else:
        # Extract from description + highlights
        highlights = raw.get("job_highlights") or {}
        qualifications = " ".join(highlights.get("Qualifications") or [])
        combined_text  = description + " " + qualifications
        required_skills = extract_skills_from_text(combined_text)

    # --- required years of experience ---
    exp_obj = raw.get("job_required_experience") or {}
    months  = exp_obj.get("required_experience_in_months")
    if months:
        required_years = months // 12
    else:
        required_years = infer_years_from_text(description)

    # --- target candidates ---
    target_candidates = infer_target_candidates_from_text(description)

    # --- role keywords ---
    highlights    = raw.get("job_highlights") or {}
    responsibilities_text = " ".join(
        str(r) for r in (highlights.get("Responsibilities") or []) if r
    )
    role_keywords = extract_role_keywords(title, description + " " + responsibilities_text)

    return {
        "title":                     title,
        "company":                   company,
        "location":                  location,
        "location_type":             location_type,
        "description":               description[:600],
        "required_skills":           required_skills,
        "required_years_experience": required_years,
        "target_candidates":         target_candidates,
        "role_keywords":             role_keywords,
        "url":                       url,
    }


def _jsearch_request(query, country_code, remote_only=False):
    """
    Make one jsearch API call. Returns list of raw job dicts (may be empty).
    """
    headers = {
        "X-RapidAPI-Key":  JSEARCH_API_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    params = {
        "query":       query,
        "page":        "1",
        "num_pages":   "2",       # 2 pages × 10 = up to 20 results
        "date_posted": "month",
    }
    if country_code:
        params["country"] = country_code
    if remote_only:
        params["remote_jobs_only"] = "true"

    print(f"\n[JOBS] jsearch query='{query}'  country={country_code or 'any'}  remote={remote_only}")
    resp = requests.get(
        "https://jsearch.p.rapidapi.com/search",
        headers=headers,
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data") or []


def fetch_jobs_from_jsearch(ai_data, preferences):
    """
    Fetch live jobs from jsearch (Google for Jobs aggregator via RapidAPI).
    Covers MENA, Europe, US, and global listings.

    Retry strategy:
      1. Specific query + country code           (e.g. "Process Improvement Analyst", LB)
      2. Broader query + same country            (e.g. "Analyst", LB)
      3. Specific query, no country filter       (global)
      4. Falls back to Adzuna, then empty list
    """
    if not JSEARCH_API_KEY:
        print("[JOBS] JSEARCH_API_KEY not set — trying Adzuna")
        return fetch_jobs_cached(ai_data, preferences)

    modes      = set(preferences.get("modes", []))
    remote_only = "remote" in modes

    query, country_code = build_jsearch_query(ai_data, preferences)

    # Build a shorter fallback query (first 1-2 meaningful words)
    query_words   = query.split()
    broader_query = " ".join(query_words[:2]) if len(query_words) > 2 else query

    attempts = [
        (query,         country_code, remote_only),   # attempt 1: precise
        (broader_query, country_code, remote_only),   # attempt 2: broader title
        (query,         None,         remote_only),   # attempt 3: no country filter
    ]

    for attempt_query, attempt_country, attempt_remote in attempts:
        try:
            raw_jobs = _jsearch_request(attempt_query, attempt_country, attempt_remote)
            print(f"[JOBS] jsearch returned {len(raw_jobs)} jobs")
            if raw_jobs:
                mapped = [map_jsearch_job(j) for j in raw_jobs if j.get("job_title")]
                if mapped:
                    return mapped
            print("   [JOBS] 0 results, trying broader search...")
        except Exception as exc:
            print(f"[JOBS] jsearch attempt failed: {exc}")
            break   # network/auth error — no point retrying

    print("[JOBS] All jsearch attempts returned 0 — falling back to Adzuna")
    return fetch_jobs_from_adzuna(ai_data, preferences)


MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

def extract_text_from_pdf(file_path):
    print(f"\nOpening PDF: {file_path}")
    text = ""

    doc = fitz.open(file_path)
    print(f"Pages: {len(doc)}")

    for i, page in enumerate(doc):
        page_text = page.get_text()
        print(f"Page {i+1} characters: {len(page_text)}")
        text += page_text

    return text.strip()


def extract_basic_info(text):
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    phone_match = re.search(r'(\+?\d[\d\-\s\(\)]{7,}\d)', text)

    result = {
        "email": email_match.group(0) if email_match else None,
        "phone": phone_match.group(0) if phone_match else None
    }

    return result


def ai_extract_cv_data(text):
    cache_key = _hashlib.md5(text[:12000].encode()).hexdigest()
    if cache_key in _ai_extract_cache:
        return _ai_extract_cache[cache_key]

    trimmed_text = text[:12000]

    prompt = f"""
    You are an AI CV parser.

    Extract structured data from the CV below.

    STRICT RULES:
    - Return ONLY valid JSON
    - Do NOT add explanation
    - Do NOT add markdown
    - If something is missing, use null or []
    - Do NOT invent information
    - Use only information explicitly present in the CV
    - If dates are unclear, keep them as they appear or use null
    - Preserve multiple entries where applicable

    Use EXACTLY this schema:

    {{
    "full_name": string or null,
    "email": string or null,
    "phone": string or null,
    "location": string or null,
    "linkedin": string or null,
    "github": string or null,
    "portfolio": string or null,
    "summary": string or null,

    "skills": [string],
    "languages": [string],
    "certifications": [string],

    "work_experience": [
        {{
        "employment_type": string or null,
        "institution_name": string or null,
        "position": string or null,
        "location": string or null,
        "start_date": string or null,
        "end_date": string or null,
        "is_current": boolean or null,
        "tasks_summary": string or null
        }}
    ],

    "education": [
        {{
        "institution": string or null,
        "degree": string or null,
        "field_of_study": string or null,
        "location": string or null,
        "start_date": string or null,
        "end_date": string or null,
        "gpa": string or null,
        "details": [string]
        }}
    ],

    "projects": [
        {{
        "name": string or null,
        "role": string or null,
        "start_date": string or null,
        "end_date": string or null,
        "technologies": [string],
        "description": string or null
        }}
    ],

    "awards": [string],
    "volunteering": [string],
    "publications": [string],

    "experience_summary": string or null
    }}

    CV TEXT:
    {trimmed_text} 
    """

    response = client.responses.create(
        model="gpt-5.4",
        input=prompt
    )

    raw_output = response.output_text.strip()

    result = json.loads(raw_output)
    _ai_extract_cache[cache_key] = result
    return result

def compute_location_score(candidate_location, preferences, job):
    candidate_location = normalize_text(candidate_location)
    job_location = normalize_text(job.get("location"))
    job_location_type = normalize_text(job.get("location_type"))

    modes = set(preferences.get("modes", []))

    # If no location preference at all, neutral
    if not modes:
        return 70, "no_preference_neutral"

    candidate_parts = {part.strip() for part in candidate_location.split(",") if part.strip()}
    job_parts = {part.strip() for part in job_location.split(",") if part.strip()}

    # remote mode: job must be remote
    if "remote" in modes and len(modes) == 1:
        if job_location_type == "remote" or job_location == "remote":
            return 100, "remote_match"
        return 0, "remote_not_matched"

    # cv_location mode only
    if "cv_location" in modes and len(modes) == 1:
        if candidate_location == job_location:
            return 100, "exact_cv_location_match"
        if candidate_parts & job_parts:
            return 100, "cv_location_component_match"
        if candidate_location in job_location or job_location in candidate_location:
            return 100, "partial_cv_location_match"
        return 0, "cv_location_not_matched"

    # willing_to_relocate only
    relocation_locations = {
        normalize_text(loc) for loc in preferences.get("relocation_locations", [])
    }
    if "willing_to_relocate" in modes and len(modes) == 1:
        if relocation_locations:
            for code in relocation_locations:
                keywords = COUNTRY_CODE_LOCATION_KEYWORDS.get(code.upper(), [code.lower()])
                for kw in keywords:
                    if kw in job_location or kw in job_location_type:
                        return 100, "relocation_location_match"
        return 0, "relocation_not_matched"

    # Multiple modes — any match is 100
    if "remote" in modes and (job_location_type == "remote" or job_location == "remote"):
        return 100, "remote_match"
    if "cv_location" in modes and candidate_location:
        if candidate_location == job_location or candidate_parts & job_parts:
            return 100, "cv_location_match_multi"
        if candidate_location in job_location or job_location in candidate_location:
            return 100, "partial_cv_location_match_multi"
    if "willing_to_relocate" in modes and relocation_locations:
        for code in relocation_locations:
            keywords = COUNTRY_CODE_LOCATION_KEYWORDS.get(code.upper(), [code.lower()])
            for kw in keywords:
                if kw in job_location or kw in job_location_type:
                    return 100, "relocation_match_multi"

    return 0, "location_not_matched"

def normalize_text(value):
    return (value or "").strip().lower()

def months_since(year, month):
    now = datetime.now()
    return (now.year - year) * 12 + (now.month - month)

def parse_month_year(date_str, blank_means_present=False):
    if not date_str:
        if blank_means_present:
            now = datetime.now()
            return now.year, now.month
        return None

    s = normalize_text(date_str)

    # Handle "Present"
    if "present" in s:
        now = datetime.now()
        return now.year, now.month

    # Handle ISO format "YYYY-MM" from type="month" inputs
    iso_match = re.fullmatch(r"(20\d{2})-(\d{2})", s.strip())
    if iso_match:
        return int(iso_match.group(1)), int(iso_match.group(2))

    # Extract year
    year_match = re.search(r"(20\d{2})", s)
    if not year_match:
        return None

    year = int(year_match.group(1))
    month = 1  # default

    # Extract month using MONTH_MAP
    for month_name, month_num in MONTH_MAP.items():
        if month_name in s:
            month = month_num
            break

    return year, month


def months_between(start_tuple, end_tuple):
    if not start_tuple or not end_tuple:
        return 0

    start_year, start_month = start_tuple
    end_year, end_month = end_tuple

    return max((end_year - start_year) * 12 + (end_month - start_month), 0)


def estimate_years_of_experience(work_experience):
    total_months = 0

    for item in work_experience:
        start = parse_month_year(item.get("start_date"))
        end   = parse_month_year(item.get("end_date"), blank_means_present=True)

        if not start:
            continue

        if not end:
            end = start

        total_months += months_between(start, end)

    # Rounding rule
    years = total_months // 12
    remaining_months = total_months % 12

    if remaining_months >= 8:
        years += 1

    return years

def infer_candidate_stage(ai_data):
    education = ai_data.get("education", [])

    latest_grad = None

    for edu in education:
        end = edu.get("end_date")
        parsed = parse_month_year(end)
        if parsed:
            if not latest_grad or parsed > latest_grad:
                latest_grad = parsed

    if not latest_grad:
        return "unknown"

    grad_year, grad_month = latest_grad
    months_diff = months_since(grad_year, grad_month)

    # still studying
    if months_diff < 0:
        return "student"

    # <= 1 year
    if months_diff <= 12:
        return "fresh_grad"

    # 1-3 years
    if months_diff <= 36:
        return "junior"

    # 3-7 years
    if months_diff <= 84:
        return "mid_level"

    # 7+ years
    return "senior"

import math

def _cosine_similarity(v1: list, v2: list) -> float:
    dot   = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def _batch_embed(texts: list) -> list:
    """Return one embedding per text via text-embedding-3-small, or [] on error."""
    if not texts:
        return []
    try:
        resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [item.embedding for item in sorted(resp.data, key=lambda x: x.index)]
    except Exception as exc:
        print(f"[WARN] Embedding API error: {exc}")
        return []


def build_candidate_role_text(cv_data):
    parts = []

    def safe_add(value):
        """Add string value to parts; flatten lists; skip non-strings."""
        if not value:
            return
        if isinstance(value, list):
            for item in value:
                if item and isinstance(item, str):
                    parts.append(item.strip())
        elif isinstance(value, str) and value.strip():
            parts.append(value.strip())

    for skill in cv_data.get("skills", []):
        safe_add(skill)

    for exp in cv_data.get("work_experience", []):
        # cover both upload (institution_name, tasks_summary) and
        # builder (organization, description) field names
        for field in ["position", "organization", "institution_name",
                      "tasks_summary", "description"]:
            safe_add(exp.get(field))

    for project in cv_data.get("projects", []):
        for field in ["title", "name", "description"]:
            safe_add(project.get(field))

    safe_add(cv_data.get("experience_summary"))
    safe_add(cv_data.get("summary"))

    return normalize_text(" ".join(parts))

def compute_role_relevance_score(cv_data, job, semantic_sim: float = None):
    """
    Score how well the candidate's background matches the job role.
    semantic_sim: pre-computed cosine similarity (0-1) from OpenAI embeddings.
                  When provided, replaces keyword matching for Part 2.
    """
    work_experience = cv_data.get("work_experience", [])
    cv_titles = {
        normalize_text(item.get("position"))
        for item in work_experience
        if item.get("position")
    }

    job_title = normalize_text(job.get("title"))
    role_keywords = [normalize_text(k) for k in job.get("role_keywords", [])]
    candidate_role_text = build_candidate_role_text(cv_data)

    # Part 1: literal title overlap (0 to 20)
    literal_score = 0
    if any(cv_title and (cv_title in job_title or job_title in cv_title) for cv_title in cv_titles):
        literal_score = 20
    else:
        job_title_words = set(job_title.split())
        best_overlap = 0
        for cv_title in cv_titles:
            cv_title_words = set(cv_title.split())
            if job_title_words:
                overlap = len(cv_title_words & job_title_words) / len(job_title_words)
                best_overlap = max(best_overlap, overlap)
        literal_score = round(best_overlap * 20)

    # Part 2: semantic similarity (0 to 80) when embeddings available,
    #         otherwise fall back to keyword matching
    matched_keywords = [kw for kw in role_keywords if kw and kw in candidate_role_text]
    if semantic_sim is not None:
        # Map cosine similarity [-1, 1] to [0, 80]; typical range is [0, 1]
        evidence_score = round(max(0.0, semantic_sim) * 80)
    elif role_keywords:
        evidence_score = round((len(matched_keywords) / len(role_keywords)) * 80)
    else:
        evidence_score = 0

    total_title_score = min(literal_score + evidence_score, 100)

    return total_title_score, matched_keywords

def match_jobs(cv_data, jobs, preferences):
    cv_skills = {normalize_text(skill) for skill in cv_data.get("skills", []) if skill}
    candidate_location = cv_data.get("location")
    work_experience = cv_data.get("work_experience", [])

    cv_titles = {
        normalize_text(item.get("position"))
        for item in work_experience
        if item.get("position")
    }

    candidate_years_experience = estimate_years_of_experience(work_experience)
    candidate_stage = infer_candidate_stage(cv_data)

    # ── Semantic role relevance: batch-embed candidate profile + all job titles ──
    candidate_role_text = build_candidate_role_text(cv_data)
    job_titles_raw = [job.get("title", "") for job in jobs]
    texts_to_embed  = [candidate_role_text] + job_titles_raw
    print(f"[EMB] Fetching semantic embeddings for {len(jobs)} jobs…")
    raw_embeddings = _batch_embed(texts_to_embed)
    if raw_embeddings and len(raw_embeddings) == len(texts_to_embed):
        candidate_emb  = raw_embeddings[0]
        job_embeddings = raw_embeddings[1:]
        print("[EMB] Embeddings ready — using semantic role scoring")
    else:
        candidate_emb  = None
        job_embeddings = [None] * len(jobs)
        print("[EMB] Embeddings unavailable — falling back to keyword scoring")

    results = []

    for idx, job in enumerate(jobs):
        required_skills = [normalize_text(skill) for skill in job.get("required_skills", [])]
        job_title = normalize_text(job.get("title"))
        required_years = job.get("required_years_experience", 0)
        target_candidates = [normalize_text(t) for t in job.get("target_candidates", [])]

        # 1) Skills score /100
        matched_skills = [skill for skill in required_skills if skill in cv_skills]
        missing_skills = [skill for skill in required_skills if skill not in cv_skills]

        if required_skills:
            skills_score = round((len(matched_skills) / len(required_skills)) * 100)
        else:
            # No extractable required skills — neutral pass.
            skills_score = 50

        # 2) Title score /100 (semantic similarity when embeddings available)
        job_emb = job_embeddings[idx]
        semantic_sim = _cosine_similarity(candidate_emb, job_emb) if (candidate_emb and job_emb) else None
        role_relevance_score, matched_role_keywords = compute_role_relevance_score(cv_data, job, semantic_sim)

        # 3) Location score /100
        location_score, location_reason = compute_location_score(
            candidate_location,
            preferences,
            job
        )

        # 4) Experience score /100
        if required_years <= 0:
            experience_score = 100
        elif candidate_years_experience >= required_years:
            experience_score = 100
        else:
            experience_score = round((candidate_years_experience / required_years) * 100)

        # 5) Graduation/student fit score /100
        if candidate_stage in target_candidates:
            stage_score = 100
        elif "fresh_grad" in target_candidates and candidate_stage == "student":
            stage_score = 70
        elif "student" in target_candidates and candidate_stage == "fresh_grad":
            stage_score = 70
        elif "junior" in target_candidates and candidate_stage == "fresh_grad":
            stage_score = 80
        else:
            stage_score = 0

        total_score = (
            skills_score +
            role_relevance_score +
            location_score +
            experience_score +
            stage_score
        )

        results.append({
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "location_type": job.get("location_type"),
            "description": job["description"],
            "url": job.get("url"),
            "match_score": total_score,
            "score_breakdown": {
                "skills_score": skills_score,
                "role_relevance_score": role_relevance_score,
                "location_score": location_score,
                "experience_score": experience_score,
                "grad_student_fit_score": stage_score
            },
            "location_reason": location_reason,
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "candidate_summary": {
                "candidate_location": candidate_location,
                "candidate_years_experience": candidate_years_experience,
                "candidate_stage": candidate_stage
            }
        })

    # ── sort all ───────────────────────────────────────────────────────────────
    all_results = sorted(results, key=lambda x: x["match_score"], reverse=True)

    # ── primary filter: 3 hard requirements ───────────────────────────────────
    filtered_results = [
        job for job in all_results
        if job["score_breakdown"]["location_score"]       >= 70
        and job["score_breakdown"]["experience_score"]    >= 50
        and job["score_breakdown"]["grad_student_fit_score"] >= 50
    ]

    # ── best-effort fallback: relax experience+fit, keep location strict ───────
    if not filtered_results and all_results:
        filtered_results = [
            job for job in all_results
            if job["score_breakdown"]["location_score"] >= 70
        ]
    # ── last resort: top 5 overall ─────────────────────────────────────────────
    if not filtered_results and all_results:
        top5 = all_results[:5]
        for job in top5:
            job["best_effort"] = True
        filtered_results = top5

    return all_results, filtered_results


print("[WORKER] Worker started...")

# ── pdflatex availability check ───────────────────────────────────────────────
try:
    r = subprocess.run(
        [PDFLATEX_BIN, "--version"],
        capture_output=True, check=True, env=_TEX_ENV
    )
    version_line = r.stdout.split("\n")[0] if r.stdout else "unknown version"
    print(f"[TEX] pdflatex found: {version_line}")
except FileNotFoundError:
    print(f"[TEX] pdflatex NOT found at: {PDFLATEX_BIN}")
    print("   Run: sudo tlmgr install titlesec parskip")
except Exception as e:
    print(f"[TEX] pdflatex check failed: {e}")

def print_matching_summary(all_job_scores, matched_jobs):
    print("\n========== ALL JOB SCORES ==========")
    for job in all_job_scores:
        print(f"\n{job['title']} at {job['company']}")
        print(f"Total Score: {job['match_score']}/500")
        print("Breakdown:")
        print(f"  Skills: {job['score_breakdown']['skills_score']}/100")
        print(f"  Title: {job['score_breakdown']['role_relevance_score']}/100")
        print(f"  Location: {job['score_breakdown']['location_score']}/100")
        print(f"  Experience: {job['score_breakdown']['experience_score']}/100")
        print(f"  Grad/Student Fit: {job['score_breakdown']['grad_student_fit_score']}/100")

    print("\n========== MATCHED JOBS ==========")
    if matched_jobs:
        for job in matched_jobs:
            print(f"{job['title']} - {job['match_score']}/500")
    else:
        print("No matched jobs")

while True:
    db = SessionLocal()
    job = None   # ensure visible in except block

    try:
        job = db.query(Job).filter(
            Job.status.in_(["pending", "pending_matching"])
        ).first()

        if job:
            print(f"\n[WORKER] Found job: {job.id}")

            original_status = job.status   # capture BEFORE overwriting
            job.status = "processing"
            db.commit()

            # ================================
            # HANDLE BOTH INPUT MODES
            # ================================

            # ================================
            # APPROVAL -> MATCHING (Feature 2 post-approval)
            # ================================
            if original_status == "pending_matching":
                print("[WORKER] CV approved — running matching for builder job...")

                ai_data = json.loads(job.ai_structured_data) if job.ai_structured_data else {}

                # Derive location preferences from the stored user_preferences if present,
                # otherwise fall back to cv_location mode using the candidate's location.
                if job.user_preferences:
                    preferences = json.loads(job.user_preferences)
                else:
                    candidate_loc = ai_data.get("location") or ""
                    preferences = {
                        "modes": ["cv_location"] if candidate_loc else [],
                        "relocation_locations": [],
                    }

                job.status_message = "Searching for matching jobs..."
                db.commit()

                live_jobs = fetch_jobs_from_jsearch(ai_data, preferences)

                job.status_message = "Scoring and ranking jobs..."
                db.commit()

                all_job_scores, matches = match_jobs(ai_data, live_jobs, preferences)

                print_matching_summary(all_job_scores, matches)

                job.matched_jobs = json.dumps(matches)
                job.status = "done"
                job.status_message = f"Done — {len(matches)} job(s) matched."
                db.commit()
                print(f"[WORKER] Matching complete for approved builder job: {job.id}")
                continue

            if job.file_path:
                # -------- Feature 1: Upload CV --------
                print("[CV] Processing uploaded CV...")

                # ── Cache check: reuse extraction from a prior completed job ───
                cached_job = (
                    db.query(Job)
                    .filter(
                        Job.user_id == job.user_id,
                        Job.file_path == job.file_path,
                        Job.id != job.id,
                        Job.ai_structured_data.isnot(None),
                    )
                    .order_by(Job.created_at.desc())
                    .first()
                )
                if cached_job and cached_job.ai_structured_data:
                    print("[CV] Reusing cached extraction from previous analysis.")
                    job.extracted_text  = cached_job.extracted_text
                    job.structured_data = cached_job.structured_data
                    job.ai_structured_data = cached_job.ai_structured_data
                    ai_data = json.loads(cached_job.ai_structured_data)
                    job.status_message = "Using cached CV analysis..."
                    db.commit()
                else:
                    job.status_message = "Extracting text from PDF..."
                    db.commit()

                    tmp_pdf = r2.download_to_tempfile(job.file_path, suffix=".pdf")
                    try:
                        text = extract_text_from_pdf(tmp_pdf)
                    finally:
                        try: os.remove(tmp_pdf)
                        except Exception: pass
                    job.extracted_text = text

                    structured = extract_basic_info(text)
                    job.structured_data = json.dumps(structured)
                    db.commit()

                    print(f"Saved extracted text length: {len(text) if text else 0}")

                    if not text:
                        job.status = "failed_no_text"
                        job.status_message = "Could not extract text from the PDF. Please check that it is not scanned/image-only."
                        db.commit()
                        continue

                    job.status_message = "Parsing CV with AI..."
                    db.commit()

                    ai_data = ai_extract_cv_data(text)

                # Basic CV validity check
                has_name = bool(ai_data.get("full_name"))
                has_contact = bool(ai_data.get("email") or ai_data.get("phone"))
                has_experience = bool(ai_data.get("work_experience") or ai_data.get("education"))
                if not (has_name or has_contact) and not has_experience:
                    job.status = "failed"
                    job.status_message = "Please upload a valid CV or resume. The file you uploaded does not appear to contain recognizable CV content."
                    db.commit()
                    continue

            elif job.candidate_profile:
                # -------- Feature 2: Build CV --------
                print("[CV] Processing form-based CV...")

                job.status_message = "Reading your CV data..."
                db.commit()

                ai_data = json.loads(job.candidate_profile)

                print("\n[CV] RAW FORM DATA:")
                print(json.dumps(ai_data, indent=2))

            else:
                raise Exception("No valid input source")

            # ================================
            # AI REFINEMENT (Feature 2 mainly, but safe for both)
            # ================================

            def refine_cv_content(data):
                cache_key = _hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()
                if cache_key in _ai_refine_cache:
                    return _ai_refine_cache[cache_key]

                prompt = f"""
You are a strict CV copy-editor. Your ONLY job is light copy-editing.

ALLOWED:
- Fix spelling and grammar mistakes
- Improve punctuation and capitalisation
- Reword awkward phrasing while preserving the exact same meaning
- Make bullet points consistently concise and parallel in style

FORBIDDEN — do NOT do any of the following:
- Invent, add, or imply any responsibility, achievement, skill, technology, or project detail not already present in the input
- Replace vague or nonsensical text with plausible-sounding professional language
- Expand short notes into full sentences if the original was just a fragment
- Remove information that was present in the input

HANDLING UNCLEAR OR NONSENSICAL TEXT:
- If a description bullet is unclear, keep it as-is with only minimal grammar fixes
- If a bullet is pure gibberish (random characters, no meaning), output it unchanged — do NOT rewrite it as a professional bullet

Return ONLY valid JSON using the EXACT same structure and keys as the input. No markdown, no explanation.

INPUT:
{json.dumps(data)}
"""

                response = client.responses.create(
                    model="gpt-5-mini",
                    input=prompt
                )

                result = json.loads(response.output_text)
                _ai_refine_cache[cache_key] = result
                return result

            if not job.ai_structured_data:
                job.status_message = "Polishing CV content with AI..."
                db.commit()
                try:
                    ai_data = refine_cv_content(ai_data)
                except Exception as refine_error:
                    print(f"[CV] Refinement failed, using original data: {refine_error}")
                job.ai_structured_data = json.dumps(ai_data)

            # ================================
            # GENERATE LATEX FOR FEATURE 2
            # ================================
            if job.candidate_profile:
                job.status_message = "Generating your CV..."
                db.commit()

                latex_source = generate_latex_cv(ai_data)
                job.generated_latex = latex_source

                os.makedirs("generated_tex", exist_ok=True)
                tex_path = f"generated_tex/{job.id}.tex"

                with open(tex_path, "w", encoding="utf-8") as f:
                    f.write(latex_source)

                job.generated_tex_path = tex_path

                # ── Compile .tex → PDF ────────────────────────────────
                pdf_path = None
                try:
                    job.status_message = "Compiling PDF..."
                    db.commit()

                    result = subprocess.run(
                        [
                            PDFLATEX_BIN,
                            "-interaction=nonstopmode",
                            "-output-directory", "generated_tex",
                            tex_path,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        env=_TEX_ENV,
                    )
                    candidate_pdf = tex_path.replace(".tex", ".pdf")
                    if os.path.exists(candidate_pdf):
                        r2_key = f"generated/{job.id}.pdf"
                        with open(candidate_pdf, "rb") as pf:
                            r2.upload_bytes(r2_key, pf.read(), "application/pdf")
                        pdf_path = r2_key
                        print(f"[TEX] PDF uploaded to R2: {r2_key}")
                        # clean up local temp files
                        for tmp in [candidate_pdf, tex_path]:
                            try: os.remove(tmp)
                            except Exception: pass
                    else:
                        print("[TEX] pdflatex ran but PDF not found.")
                        print("── pdflatex stdout (last 1000 chars) ──")
                        print(result.stdout[-1000:])
                        print("── pdflatex stderr ──")
                        print(result.stderr[-500:])
                except FileNotFoundError:
                    print(f"[TEX] pdflatex binary not found at: {PDFLATEX_BIN}")
                    print("   Install BasicTeX and run: sudo tlmgr install titlesec parskip")
                except Exception as compile_err:
                    print(f"[TEX] PDF compilation failed: {compile_err}")

                job.generated_pdf_path = pdf_path
                job.status = "cv_generated"
                job.status_message = "CV ready! Review it below before finding matching jobs."
                db.commit()

                print(f"[TEX] LaTeX CV generated for job {job.id}")
                print(f"[TEX] Saved .tex file at: {tex_path}")

                continue  # skip matching until approved

            # ================================
            # MATCHING
            # ================================

            preferences = json.loads(job.user_preferences) if job.user_preferences else {
                "modes": [],
                "relocation_locations": []
            }

            job.status_message = "Searching for matching jobs..."
            db.commit()

            live_jobs = fetch_jobs_from_jsearch(ai_data, preferences)

            job.status_message = "Scoring and ranking jobs..."
            db.commit()

            all_job_scores, matches = match_jobs(ai_data, live_jobs, preferences)

            print_matching_summary(all_job_scores, matches)

            job.matched_jobs = json.dumps(matches)
            job.status = "done"
            job.status_message = f"Done — {len(matches)} job(s) found."
            db.commit()

            print(f"[WORKER] Finished job: {job.id}\n")

    except Exception as e:
        print(f"[ERROR] {e}")
        traceback.print_exc()
        try:
            if job is not None:
                job.status = "failed"
                job.status_message = "Processing failed — please try again."
                db.commit()
        except Exception:
            pass

    finally:
        db.close()

    time.sleep(2 if job else 10)