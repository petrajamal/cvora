"""
latex_gen.py — LaTeX CV generation helpers.

Extracted so both worker.py (background queue) and main.py (/preview-cv fast
endpoint) can share the same template without duplicating code.
"""

import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime

# ── TeX environment ────────────────────────────────────────────────────────────
# BasicTeX on macOS installs to /Library/TeX/texbin which is often not on PATH
# for subprocesses launched from Python.  Build an env that always includes it.

_TEX_BIN_DIR = "/Library/TeX/texbin"
PDFLATEX_BIN = (
    shutil.which("pdflatex")
    or os.path.join(_TEX_BIN_DIR, "pdflatex")
)

_TEX_ENV = os.environ.copy()
if _TEX_BIN_DIR not in _TEX_ENV.get("PATH", ""):
    _TEX_ENV["PATH"] = _TEX_BIN_DIR + os.pathsep + _TEX_ENV.get("PATH", "")

# ── Small text helpers ─────────────────────────────────────────────────────────

# ── Bullet line estimator ──────────────────────────────────────────────────────
# Calibrated for 10pt Computer Modern on A4 (210mm) with left=0.65in,
# right=0.65in margins (text width ≈ 177mm) and \leftmargin 1.4em bullet
# indentation. Verified: "...early intervention," (99 chars) fits on line 1;
# "and tailored support plans." wraps to line 2 — so 100 chars/line is accurate.
_BULLET_CPL = 100  # characters per bullet-content line


def estimate_bullet_lines(text: str) -> int:
    """Estimate how many printed lines a single bullet occupies."""
    words = text.split()
    if not words:
        return 0
    lines, col = 1, 0
    for word in words:
        wlen = len(word)
        if col == 0:
            col = wlen
        elif col + 1 + wlen > _BULLET_CPL:
            lines += 1
            col = wlen
        else:
            col += 1 + wlen
    return lines


def last_line_word_count(text: str) -> int:
    """Return the number of words sitting on the last printed line of a bullet."""
    words = text.split()
    if not words:
        return 0
    col, count = 0, 0
    for word in words:
        wlen = len(word)
        if col == 0:
            col = wlen
            count = 1
        elif col + 1 + wlen > _BULLET_CPL:
            col = wlen
            count = 1
        else:
            col += 1 + wlen
            count += 1
    return count


_LINES_PER_PAGE = 52  # usable content lines for 10pt A4 with 0.55in/0.65in margins


def estimate_cv_overlong(profile: dict) -> bool:
    """Heuristic: True if profile is likely to overflow one printed page."""
    lines = 4.0  # header + surrounding spacing

    summary = (profile.get("summary") or "").strip()
    if summary:
        lines += 2.5 + estimate_bullet_lines(summary)

    sg = profile.get("skill_groups") or {}
    for section in ("work_experience", "education", "projects", "extracurriculars"):
        entries = [e for e in (profile.get(section) or []) if not e.get("hidden")]
        if not entries:
            continue
        lines += 2.0
        for entry in entries:
            lines += 1.5
            for bullet in (entry.get("description") or []):
                lines += estimate_bullet_lines(bullet)
            lines += 0.5

    skill_rows = sum(
        1 for k in ("technical", "tools", "soft")
        if sg.get(k) and not sg.get(f"{k}_hidden")
    )
    if skill_rows or profile.get("skills"):
        lines += 2.0 + max(skill_rows, 1)

    certs = [c for c in (profile.get("certifications") or []) if not c.get("hidden")]
    if certs:
        lines += 2.0 + len(certs) * 0.9

    awards = [a for a in (profile.get("awards") or []) if not a.get("hidden")]
    if awards:
        lines += 2.0 + len(awards) * 0.9

    if (profile.get("languages") or []) and not profile.get("languages_hidden"):
        lines += 2.5

    return lines > _LINES_PER_PAGE


_MONTH_MAP = {
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

_MONTH_ABBREV = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def parse_month_year(date_str, blank_means_present=False):
    if not date_str:
        if blank_means_present:
            now = datetime.now()
            return now.year, now.month
        return None

    s = _norm(date_str)

    if "present" in s:
        now = datetime.now()
        return now.year, now.month

    iso_match = re.fullmatch(r"(20\d{2})-(\d{2})", s.strip())
    if iso_match:
        return int(iso_match.group(1)), int(iso_match.group(2))

    year_match = re.search(r"(20\d{2})", s)
    if not year_match:
        return None
    year = int(year_match.group(1))
    month = 1
    for name, num in _MONTH_MAP.items():
        if name in s:
            month = num
            break
    return year, month


def fmt_date(date_str, blank_means_present=False):
    """Convert a date string to 'Mmm YYYY' display form."""
    if not date_str:
        return "Present" if blank_means_present else ""
    s = (date_str or "").strip().lower()
    if "present" in s:
        return "Present"
    parsed = parse_month_year(date_str, blank_means_present)
    if not parsed:
        return str(date_str)
    year, month = parsed
    month = max(1, min(12, month))
    return f"{_MONTH_ABBREV[month]} {year}"


def months_between(start_tuple, end_tuple):
    if not start_tuple or not end_tuple:
        return 0
    sy, sm = start_tuple
    ey, em = end_tuple
    return max((ey - sy) * 12 + (em - sm), 0)


# ── LaTeX escaping ─────────────────────────────────────────────────────────────

def latex_escape(text):
    if text is None:
        return ""
    replacements = {
        "\\": r"\textbackslash{}",
        "&":  r"\&",
        "%":  r"\%",
        "$":  r"\$",
        "#":  r"\#",
        "_":  r"\_",
        "{":  r"\{",
        "}":  r"\}",
        "~":  r"\textasciitilde{}",
        "^":  r"\textasciicircum{}",
    }
    result = str(text)
    for old, new in replacements.items():
        result = result.replace(old, new)
    return result


def render_bullets(items, max_bullets=None):
    if not items:
        return ""
    if max_bullets is not None:
        items = items[:max_bullets]

    def _fmt(item):
        item = item.strip()
        if not item:
            return None
        if item[-1] not in ".!?)":
            item += "."
        return f"\\item {latex_escape(item)}"

    bullet_lines = "\n".join(line for line in (_fmt(i) for i in items) if line)
    if not bullet_lines.strip():
        return ""
    return f"""
        \\begin{{itemize}}
        {bullet_lines}
        \\end{{itemize}}
        """


def ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


# ── CV generator ──────────────────────────────────────────────────────────────

def _sort_chrono(entries, end_key="end_date", start_key="start_date"):
    """Sort entries newest-first by end date then start date."""
    def _key(e):
        end   = (e.get(end_key)   or "").strip().lower()
        start = (e.get(start_key) or "").strip().lower()
        if not end or "present" in end:
            end = "9999-12"
        return (end, start)
    return sorted(entries, key=_key, reverse=True)


def compute_allow_two_pages(profile: dict) -> bool:
    """Return True iff the candidate has ≥7 years experience across ≥3 jobs."""
    work_experience = profile.get("work_experience") or []
    total = 0
    for exp in work_experience:
        s = parse_month_year(exp.get("start_date", ""))
        e_raw = (exp.get("end_date") or "").strip()
        if not e_raw or "present" in e_raw.lower():
            now = datetime.now()
            e = (now.year, now.month)
        else:
            e = parse_month_year(e_raw)
        if s and e:
            total += months_between(s, e)
    return (total // 12) >= 7 and len(work_experience) >= 3


def generate_latex_cv(profile: dict, max_bullets_override: int | None = None, compact_skills: bool = False) -> str:
    name     = latex_escape(profile.get("full_name", ""))
    links    = profile.get("links", []) or []
    summary  = latex_escape(profile.get("summary", ""))

    education       = _sort_chrono(profile.get("education", [])       or [], "end_date",  "start_date")
    work_experience = _sort_chrono(profile.get("work_experience", []) or [], "end_date",  "start_date")
    projects        = _sort_chrono(profile.get("projects", [])        or [], "end_date",  "start_date")
    skills          = profile.get("skills", [])          or []
    extracurriculars = _sort_chrono(profile.get("extracurriculars", []) or [], "date", "date")
    certifications  = profile.get("certifications", [])  or []
    awards          = profile.get("awards", [])          or []
    languages       = profile.get("languages", [])       or []

    # ── Page limit ────────────────────────────────────────────────────────────
    allow_two_pages = compute_allow_two_pages(profile)
    if max_bullets_override is not None:
        max_bullets = max_bullets_override
    else:
        max_bullets = None if allow_two_pages else 3

    # ── Section label helper (custom names from UI) ────────────────────────────
    _sl = profile.get("section_labels") or {}

    def sec(key, default):
        custom = (_sl.get(key) or "").strip()
        return latex_escape(custom if custom else default)

    # ── inner helpers ──────────────────────────────────────────────────────────

    def entry_header(left_bold, right, subtitle=""):
        # Title line ends with \\[-2pt] to pull subtitle close (tight within-entry spacing).
        # Subtitle has NO trailing \\ — the caller closes with \par so that the
        # following \vspace{3pt} is a clean between-paragraph skip, not a within-line skip.
        if right:
            first_line = f"\\textbf{{{left_bold}}} \\hfill \\small \\textit{{{right}}} \\\\[-2pt]"
        else:
            first_line = f"\\textbf{{{left_bold}}} \\\\[-2pt]"
        if subtitle:
            return f"{first_line}\n\\small\\textit{{{subtitle}}}"
        return first_line

    def date_range(start, end, open_ended=False):
        start_fmt = fmt_date(start) if start else ""
        end_fmt   = fmt_date(end) if end else ("Present" if open_ended else "")
        parts = [p for p in [start_fmt, end_fmt] if p]
        return " -- ".join(parts)

    # ── Education ──────────────────────────────────────────────────────────────
    education_blocks = []
    for edu in education:
        if edu.get("hidden"): continue
        institution = latex_escape(edu.get("institution", ""))
        degree      = latex_escape(edu.get("degree", ""))
        field       = latex_escape(edu.get("field_of_study") or "")
        start       = edu.get("start_date", "")
        end         = edu.get("end_date", "")
        gpa_raw     = (edu.get("gpa") or "").strip()
        # Strip any "GPA: " prefix already baked in by the frontend before re-labelling
        gpa_display = gpa_raw[5:].strip() if gpa_raw.upper().startswith("GPA:") else gpa_raw
        gpa_display = latex_escape(gpa_display)
        desc        = ensure_list(edu.get("description") or edu.get("details"))

        degree_line = " -- ".join(p for p in [degree, field] if p)
        extras = []
        if gpa_display:
            extras.append(f"GPA: {gpa_display}")
        sep = " $\\cdot$ "
        subtitle = degree_line + (f"{sep}{sep.join(extras)}" if extras else "")

        education_blocks.append(
            entry_header(institution, date_range(start, end, open_ended=True), subtitle)
            + (render_bullets(desc, max_bullets) + "\n\\vspace{3pt}\n"
               if desc else "\\par\n\\vspace{3pt}\n")
        )

    # ── Work Experience ────────────────────────────────────────────────────────
    experience_blocks = []
    for exp in work_experience:
        if exp.get("hidden"): continue
        org      = latex_escape(exp.get("organization") or exp.get("institution_name") or "")
        position = latex_escape(exp.get("position", ""))
        loc      = latex_escape(exp.get("location") or "")
        start    = exp.get("start_date", "")
        end      = exp.get("end_date", "")
        bullets  = ensure_list(
            exp.get("description") or exp.get("description_points") or exp.get("tasks_summary")
        )

        loc_str = f"\\faMapMarkerAlt~{loc}" if loc else ""
        subtitle_parts = [p for p in [org, loc_str] if p]
        subtitle = " $\\cdot$ ".join(subtitle_parts)

        experience_blocks.append(
            entry_header(position or org, date_range(start, end, open_ended=True), subtitle)
            + (render_bullets(bullets, max_bullets) + "\n\\vspace{3pt}\n"
               if bullets else "\\par\n\\vspace{3pt}\n")
        )

    # ── Projects ───────────────────────────────────────────────────────────────
    project_blocks = []
    for proj in projects:
        if proj.get("hidden"): continue
        title        = latex_escape(proj.get("title") or proj.get("name") or "")
        role         = latex_escape(proj.get("role") or "")
        technologies = proj.get("technologies") or []
        desc         = ensure_list(proj.get("description"))
        link_raw     = (proj.get("link") or "").strip()
        start        = proj.get("start_date") or ""
        end          = proj.get("end_date") or ""

        tech_line = ", ".join(latex_escape(t) for t in technologies if t)
        subtitle_parts = [p for p in [role, tech_line] if p]
        subtitle = " $\\cdot$ ".join(subtitle_parts)
        date_str = date_range(start, end) if (start or end) else ""

        link_str = ""
        right = date_str
        if link_raw:
            link_display_raw = (proj.get("link_display") or "").strip()
            link_label = latex_escape(link_display_raw) if link_display_raw else latex_escape(link_raw)
            href = f"\\textcolor{{cvlink}}{{\\href{{{latex_escape(link_raw)}}}{{{link_label}}}}}"
            if not date_str:
                right = href  # italic applied by entry_header's \textit{right}
            else:
                link_str = f"\n\\textit{{{href}}}\\\\"

        project_blocks.append(
            entry_header(title, right, subtitle)
            + link_str
            + (render_bullets(desc, max_bullets) + "\n\\vspace{3pt}\n"
               if desc else "\\par\n\\vspace{3pt}\n")
        )

    # ── Extracurriculars ───────────────────────────────────────────────────────
    extracurricular_blocks = []
    for item in extracurriculars:
        if item.get("hidden"): continue
        title        = latex_escape(item.get("title", ""))
        role         = latex_escape(item.get("role", ""))
        organization = latex_escape(item.get("organization", ""))
        date_raw     = item.get("date") or ""
        date         = fmt_date(date_raw) if date_raw else ""
        desc         = ensure_list(item.get("description"))

        subtitle = " $\\cdot$ ".join(p for p in [role, organization] if p)

        url_raw  = (item.get("url") or "").strip()
        url_str  = ""
        right_ex = date
        if url_raw:
            url_display_raw = (item.get("url_display") or "").strip()
            url_label = latex_escape(url_display_raw) if url_display_raw else latex_escape(url_raw)
            href_ex = f"\\textcolor{{cvlink}}{{\\href{{{latex_escape(url_raw)}}}{{{url_label}}}}}"
            if not date:
                right_ex = href_ex  # italic applied by entry_header's \textit{right}
            else:
                url_str = f"\n\\textit{{{href_ex}}}\\\\"

        extracurricular_blocks.append(
            entry_header(title, right_ex, subtitle)
            + url_str
            + (render_bullets(desc, max_bullets) + "\n\\vspace{3pt}\n"
               if desc else "\\par\n\\vspace{3pt}\n")
        )

    # ── Certifications ─────────────────────────────────────────────────────────
    certification_lines = []
    for cert in certifications:
        if isinstance(cert, dict) and cert.get("hidden"): continue
        if isinstance(cert, dict):
            t   = latex_escape(cert.get("title", ""))
            d   = latex_escape(cert.get("date") or "")
            org = latex_escape(cert.get("organization") or "")
            line = " $\\cdot$ ".join(p for p in [t, org, d] if p)
            if line:
                certification_lines.append(line)
        else:
            certification_lines.append(latex_escape(cert))

    # ── Awards ─────────────────────────────────────────────────────────────────
    award_lines = []
    for award in awards:
        if isinstance(award, dict) and award.get("hidden"): continue
        if isinstance(award, dict):
            t    = latex_escape(award.get("title", ""))
            d    = latex_escape(award.get("date") or "")
            inst = latex_escape(award.get("institution") or "")
            line = " $\\cdot$ ".join(p for p in [t, inst, d] if p)
            if line:
                award_lines.append(line)
        else:
            award_lines.append(latex_escape(award))

    # ── Languages ──────────────────────────────────────────────────────────────
    language_lines = []
    if not profile.get("languages_hidden"):
        for lang in languages:
            if isinstance(lang, dict) and lang.get("hidden"): continue
            if isinstance(lang, dict):
                name_lang = latex_escape(lang.get("language", ""))
                level     = latex_escape(lang.get("proficiency") or "")
                line = " -- ".join(p for p in [name_lang, level] if p)
                if line:
                    language_lines.append(line)
            else:
                language_lines.append(latex_escape(lang))

    # ── Font / margins ─────────────────────────────────────────────────────────
    font_size   = "10pt"
    top_margin  = "0.55in" if allow_two_pages else "0.50in"
    side_margin = "0.65in" if allow_two_pages else "0.60in"

    # ── Skill groups ───────────────────────────────────────────────────────────
    skill_groups     = profile.get("skill_groups") or {}
    technical_skills = skill_groups.get("technical") or []
    tools_skills     = skill_groups.get("tools")     or []
    soft_skills      = skill_groups.get("soft")      or []

    # ── Collect visible skill lists ────────────────────────────────────────────
    vis_technical = (technical_skills if not skill_groups.get("technical_hidden") else [])
    vis_tools     = (tools_skills     if not skill_groups.get("tools_hidden")     else [])
    vis_soft      = (soft_skills      if not skill_groups.get("soft_hidden")      else [])

    # compact_skills: try flat one-line output (no category labels) when fitting 1 page
    if compact_skills and (vis_technical or vis_tools or vis_soft):
        all_flat = [s for grp in (vis_technical, vis_tools, vis_soft) for s in grp if s]
        flat_line = ", ".join(latex_escape(s) for s in all_flat)
        if len(flat_line) <= _BULLET_CPL:
            skills_section = (
                f"\\section*{{{sec('skills', 'Skills')}}}\n"
                f"{flat_line}\n\\par\\vspace{{6pt}}\n"
            )
        else:
            compact_skills = False  # too long — fall through to normal tabular

    if not compact_skills:
        skills_rows = []
        _tech_lbl = latex_escape((skill_groups.get("technical_label") or "Technical").rstrip(":"))
        _tools_lbl = latex_escape((skill_groups.get("tools_label") or "Tools").rstrip(":"))
        _soft_lbl  = latex_escape((skill_groups.get("soft_label") or "Soft Skills").rstrip(":"))
        if vis_technical:
            skills_rows.append(
                f"\\textbf{{{_tech_lbl}:}} & "
                f"{', '.join(latex_escape(s) for s in vis_technical)} \\\\"
            )
        if vis_tools:
            skills_rows.append(
                f"\\textbf{{{_tools_lbl}:}} & "
                f"{', '.join(latex_escape(s) for s in vis_tools)} \\\\"
            )
        if vis_soft:
            skills_rows.append(
                f"\\textbf{{{_soft_lbl}:}} & "
                f"{', '.join(latex_escape(s) for s in vis_soft)} \\\\"
            )
        if not skills_rows and skills:
            flat = ", ".join(latex_escape(sk) for sk in skills if sk)
            skills_rows.append(f"\\textbf{{Skills:}} & {flat} \\\\")

        if skills_rows:
            rows_latex = "\n".join(skills_rows)
            if rows_latex.endswith("\\\\"):
                rows_latex = rows_latex[:-2]
            skills_section = (
                f"\\section*{{{sec('skills', 'Skills')}}}\n"
                "\\begin{tabular}{@{}p{2.2cm}p{\\dimexpr\\linewidth-2.2cm\\relax}@{}}\n"
                + rows_latex
                + "\n\\end{tabular}\n\\par\\vspace{6pt}\n"
            )
        else:
            skills_section = ""

    # ── Header lines: order is location · email · phone, then links A-Z ──────
    _LINK_ICONS = {
        "linkedin":  "\\faLinkedin",
        "github":    "\\faGithub",
        "portfolio": "\\faGlobe",
        "website":   "\\faGlobe",
        "twitter":   "\\faTwitter",
        "behance":   "\\faBehance",
        "dribbble":  "\\faDribbble",
        "leetcode":  "\\faCode",
    }

    contact_items = []
    if profile.get("location"):
        contact_items.append(f"\\faMapMarkerAlt~{latex_escape(profile['location'])}")
    if profile.get("email"):
        contact_items.append(
            f"\\faEnvelope~\\textcolor{{cvlink}}{{\\textit{{\\href{{mailto:{latex_escape(profile['email'])}}}"
            f"{{{latex_escape(profile['email'])}}}}}}}"
        )
    if profile.get("phone"):
        contact_items.append(f"\\faPhone~{latex_escape(profile['phone'])}")

    # Build link items, sort alphabetically by display text
    _raw_links = []
    for link in links:
        url_raw     = (link.get("url") or "").strip()
        display_raw = (link.get("display") or link.get("url") or "").strip()
        link_type   = (link.get("type") or "other").lower()
        if url_raw:
            label      = latex_escape(display_raw or url_raw)
            escaped_url = latex_escape(url_raw)
            icon       = _LINK_ICONS.get(link_type, "")
            icon_prefix = f"{icon}~" if icon else ""
            _raw_links.append((display_raw.lower(), f"{icon_prefix}\\textcolor{{cvlink}}{{\\textit{{\\href{{{escaped_url}}}{{{label}}}}}}}"))
    _raw_links.sort(key=lambda x: x[0])
    formatted_links_header = [item for _, item in _raw_links]

    header_line1 = " $\\cdot$ ".join(contact_items)
    header_line2 = " $\\cdot$ ".join(formatted_links_header)

    # Single header line when combined text is short enough; else split
    _contact_raw = " ".join(filter(None, [
        profile.get("location", ""), profile.get("email", ""), profile.get("phone", ""),
    ]))
    _links_raw = " ".join(filter(None, [
        (lk.get("display") or lk.get("url") or "") for lk in links if lk.get("url")
    ]))
    if header_line1 and header_line2 and len(_contact_raw) + len(_links_raw) <= 90:
        header_block = f"\\small {header_line1} $\\cdot$ {header_line2}"
    elif header_line1 and header_line2:
        header_block = f"\\small {header_line1} \\\\[2pt]\n  \\small {header_line2}"
    elif header_line1:
        header_block = f"\\small {header_line1}"
    elif header_line2:
        header_block = f"\\small {header_line2}"
    else:
        header_block = ""

    # ── Section strings ────────────────────────────────────────────────────────
    summary_section = f"\\section*{{Summary}}\n\\small {summary}\n" if summary else ""

    education_section = (
        f"\\section*{{{sec('education', 'Education')}}}\n" + "".join(education_blocks)
    ) if education_blocks else ""

    experience_section = (
        f"\\section*{{{sec('experience', 'Work Experience')}}}\n" + "".join(experience_blocks)
    ) if experience_blocks else ""

    projects_section = (
        f"\\section*{{{sec('projects', 'Projects')}}}\n" + "".join(project_blocks)
    ) if project_blocks else ""

    _extracurricular_label = sec("extracurriculars", "Extracurricular Activities & Leadership")
    extracurricular_section = (
        f"\\section*{{{_extracurricular_label}}}\n"
        + "".join(extracurricular_blocks)
    ) if extracurricular_blocks else ""

    certifications_section = ""
    if certification_lines:
        certifications_section = (
            f"\\section*{{{sec('certifications', 'Certifications')}}}\n\\begin{{itemize}}\n"
            + "\n".join(f"  \\item {line}" for line in certification_lines)
            + "\n\\end{itemize}\n"
        )

    _awards_label = sec("awards", "Awards & Achievements")
    awards_section = ""
    if award_lines:
        awards_section = (
            f"\\section*{{{_awards_label}}}\n\\begin{{itemize}}\n"
            + "\n".join(f"  \\item {line}" for line in award_lines)
            + "\n\\end{itemize}\n"
        )

    languages_section = (
        f"\\section*{{{sec('languages', 'Languages')}}}\n"
        + " $\\cdot$ ".join(language_lines) + "\n"
    ) if language_lines else ""


    # ── Assemble document ──────────────────────────────────────────────────────
    latex = f"""
\\documentclass[{font_size},a4paper]{{article}}
\\usepackage[top={top_margin},bottom={top_margin},left={side_margin},right={side_margin}]{{geometry}}
\\usepackage[hidelinks,colorlinks=false]{{hyperref}}
\\usepackage{{xcolor}}
\\usepackage{{fontawesome5}}
\\definecolor{{cvlink}}{{RGB}}{{37,99,235}}

% ── Section headings: bold small-caps + full-width rule (no titlesec needed)
\\makeatletter
\\renewcommand\\section{{\\@ifstar\\mcsect\\mcsect}}
\\newcommand\\mcsect[1]{{%
  \\vspace{{5pt}}%
  \\noindent{{\\large\\bfseries\\scshape #1}}%
  \\par\\vspace{{1pt}}\\noindent\\rule{{\\linewidth}}{{0.4pt}}\\vspace{{3pt}}%
  \\nopagebreak[4]%
}}
% ── List spacing: tight bullets without enumitem ──────────────────────────
\\def\\@listi{{\\leftmargin 1.4em \\topsep 1pt \\parsep 0pt \\itemsep 1pt}}
\\let\\@listI\\@listi
\\makeatother

\\pagestyle{{empty}}
\\setlength{{\\parindent}}{{0pt}}
\\setlength{{\\parskip}}{{0pt}}

\\begin{{document}}

% ════════════════════════════════════════════════════
%  HEADER
% ════════════════════════════════════════════════════
\\begin{{center}}
  {{\\LARGE \\textbf{{{name}}}}} \\\\[4pt]
  {header_block}
\\end{{center}}

\\vspace{{2pt}}

{summary_section}
{education_section}
{experience_section}
{projects_section}
{extracurricular_section}
{certifications_section}
{awards_section}
{languages_section}
{skills_section}

\\end{{document}}
"""
    return latex.strip()


# ── PDF compilation ────────────────────────────────────────────────────────────

def compile_to_pdf(latex_source: str) -> bytes:
    """
    Compile a LaTeX string to PDF bytes using pdflatex.
    Raises RuntimeError on failure with the pdflatex stderr as the message.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "cv.tex")
        pdf_path = os.path.join(tmpdir, "cv.pdf")

        with open(tex_path, "w", encoding="utf-8") as fh:
            fh.write(latex_source)

        result = subprocess.run(
            [
                PDFLATEX_BIN,
                "-interaction=nonstopmode",
                "-output-directory", tmpdir,
                tex_path,
            ],
            capture_output=True,
            timeout=60,
            env=_TEX_ENV,
        )
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")

        if not os.path.exists(pdf_path):
            # Give a clear message for unsupported scripts (Arabic, CJK, etc.)
            combined = stdout + stderr
            if any(ord(c) > 0x036F for c in latex_source[:500]):
                raise RuntimeError(
                    "The CV builder does not support non-Latin scripts (Arabic, CJK, etc.). "
                    "Please use English or Latin-alphabet text."
                )
            raise RuntimeError(f"Preview generation failed:\n{combined[-1500:]}")

        with open(pdf_path, "rb") as fh:
            return fh.read()


def compile_to_pdf_checked(latex_source: str) -> tuple:
    """Like compile_to_pdf but also returns page count parsed from pdflatex stdout."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "cv.tex")
        pdf_path = os.path.join(tmpdir, "cv.pdf")

        with open(tex_path, "w", encoding="utf-8") as fh:
            fh.write(latex_source)

        result = subprocess.run(
            [PDFLATEX_BIN, "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path],
            capture_output=True, timeout=60, env=_TEX_ENV,
        )
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")

        if not os.path.exists(pdf_path):
            combined = stdout + stderr
            if any(ord(c) > 0x036F for c in latex_source[:500]):
                raise RuntimeError(
                    "The CV builder does not support non-Latin scripts (Arabic, CJK, etc.). "
                    "Please use English or Latin-alphabet text."
                )
            raise RuntimeError(f"Preview generation failed:\n{combined[-1500:]}")

        m = re.search(r"Output written on .+? \((\d+) page", stdout)
        page_count = int(m.group(1)) if m else 1

        with open(pdf_path, "rb") as fh:
            return fh.read(), page_count
