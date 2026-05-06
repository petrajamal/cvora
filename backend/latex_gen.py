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


def render_bullets(items):
    if not items:
        return ""
    bullet_lines = "\n".join(
        f"\\item {latex_escape(item)}" for item in items if item
    )
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

def generate_latex_cv(profile: dict) -> str:
    name     = latex_escape(profile.get("full_name", ""))
    links    = profile.get("links", []) or []
    summary  = latex_escape(profile.get("summary", ""))

    education       = profile.get("education", [])       or []
    work_experience = profile.get("work_experience", []) or []
    projects        = profile.get("projects", [])        or []
    skills          = profile.get("skills", [])          or []
    extracurriculars = profile.get("extracurriculars", []) or []
    certifications  = profile.get("certifications", [])  or []
    awards          = profile.get("awards", [])          or []
    languages       = profile.get("languages", [])       or []

    # ── inner helpers ──────────────────────────────────────────────────────────

    def entry_header(left_bold, right, subtitle=""):
        if right:
            first_line = f"\\textbf{{{left_bold}}} \\hfill \\small {right} \\\\"
        else:
            first_line = f"\\textbf{{{left_bold}}} \\\\"
        lines = [first_line]
        if subtitle:
            lines.append(f"\\small\\textit{{{subtitle}}} \\\\[-2pt]")
        return "\n".join(lines)

    def date_range(start, end, open_ended=False):
        end_display = end if end else ("Present" if open_ended else "")
        parts = [p for p in [start, end_display] if p]
        return " -- ".join(parts)

    # ── Education ──────────────────────────────────────────────────────────────
    education_blocks = []
    for edu in education:
        institution = latex_escape(edu.get("institution", ""))
        degree      = latex_escape(edu.get("degree", ""))
        field       = latex_escape(edu.get("field_of_study") or "")
        start       = latex_escape(edu.get("start_date", ""))
        end         = latex_escape(edu.get("end_date", ""))
        gpa         = latex_escape(edu.get("gpa") or "")

        degree_line = " -- ".join(p for p in [degree, field] if p)
        extras = []
        if gpa:
            extras.append(f"GPA: {gpa}")
        sep = " $\\cdot$ "
        subtitle = degree_line + (f"{sep}{sep.join(extras)}" if extras else "")

        education_blocks.append(
            entry_header(institution, date_range(start, end, open_ended=True), subtitle)
            + "\n\\vspace{3pt}\n"
        )

    # ── Work Experience ────────────────────────────────────────────────────────
    experience_blocks = []
    for exp in work_experience:
        org      = latex_escape(exp.get("organization") or exp.get("institution_name") or "")
        position = latex_escape(exp.get("position", ""))
        loc      = latex_escape(exp.get("location") or "")
        start    = latex_escape(exp.get("start_date", ""))
        end      = latex_escape(exp.get("end_date", ""))
        bullets  = ensure_list(
            exp.get("description") or exp.get("description_points") or exp.get("tasks_summary")
        )

        subtitle_parts = [p for p in [org, loc] if p]
        subtitle = " $\\cdot$ ".join(subtitle_parts)

        experience_blocks.append(
            entry_header(position or org, date_range(start, end, open_ended=True), subtitle)
            + render_bullets(bullets)
            + "\n\\vspace{3pt}\n"
        )

    # ── Projects ───────────────────────────────────────────────────────────────
    project_blocks = []
    for proj in projects:
        title        = latex_escape(proj.get("title") or proj.get("name") or "")
        role         = latex_escape(proj.get("role") or "")
        technologies = proj.get("technologies") or []
        desc         = ensure_list(proj.get("description"))
        link_raw     = (proj.get("link") or "").strip()

        tech_line = ", ".join(latex_escape(t) for t in technologies if t)
        subtitle_parts = [p for p in [role, tech_line] if p]
        subtitle = " $\\cdot$ ".join(subtitle_parts)

        link_str = ""
        if link_raw:
            link_str = (
                f"\n\\href{{{latex_escape(link_raw)}}}"
                f"{{\\small \\texttt{{{latex_escape(link_raw)}}}}}\\\\"
            )

        project_blocks.append(
            entry_header(title, "", subtitle)
            + link_str
            + render_bullets(desc)
            + "\n\\vspace{3pt}\n"
        )

    # ── Extracurriculars ───────────────────────────────────────────────────────
    extracurricular_blocks = []
    for item in extracurriculars:
        title        = latex_escape(item.get("title", ""))
        role         = latex_escape(item.get("role", ""))
        organization = latex_escape(item.get("organization", ""))
        date         = latex_escape(item.get("date") or "")
        desc         = ensure_list(item.get("description"))

        subtitle = " $\\cdot$ ".join(p for p in [role, organization] if p)

        extracurricular_blocks.append(
            entry_header(title, date, subtitle)
            + render_bullets(desc)
            + "\n\\vspace{3pt}\n"
        )

    # ── Certifications ─────────────────────────────────────────────────────────
    certification_lines = []
    for cert in certifications:
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
    for lang in languages:
        if isinstance(lang, dict):
            name_lang = latex_escape(lang.get("language", ""))
            level     = latex_escape(lang.get("proficiency") or "")
            line = " -- ".join(p for p in [name_lang, level] if p)
            if line:
                language_lines.append(line)
        else:
            language_lines.append(latex_escape(lang))

    # ── Page limit rule ────────────────────────────────────────────────────────
    total_exp_months = 0
    for exp in work_experience:
        s = parse_month_year(exp.get("start_date", ""))
        e_raw = (exp.get("end_date") or "").strip()
        if not e_raw or "present" in e_raw.lower():
            now = datetime.now()
            e = (now.year, now.month)
        else:
            e = parse_month_year(e_raw)
        if s and e:
            total_exp_months += months_between(s, e)
    years_exp = total_exp_months // 12
    allow_two_pages = (years_exp >= 7 and len(work_experience) >= 3)
    font_size   = "10pt"
    top_margin  = "0.55in" if allow_two_pages else "0.50in"
    side_margin = "0.65in" if allow_two_pages else "0.60in"

    # ── Skill groups ───────────────────────────────────────────────────────────
    skill_groups     = profile.get("skill_groups") or {}
    technical_skills = skill_groups.get("technical") or []
    tools_skills     = skill_groups.get("tools")     or []
    soft_skills      = skill_groups.get("soft")      or []

    skills_rows = []
    if technical_skills:
        skills_rows.append(
            f"\\textbf{{Technical:}} & "
            f"{', '.join(latex_escape(s) for s in technical_skills if s)} \\\\"
        )
    if tools_skills:
        skills_rows.append(
            f"\\textbf{{Tools:}} & "
            f"{', '.join(latex_escape(s) for s in tools_skills if s)} \\\\"
        )
    if soft_skills:
        skills_rows.append(
            f"\\textbf{{Soft Skills:}} & "
            f"{', '.join(latex_escape(s) for s in soft_skills if s)} \\\\"
        )
    if not skills_rows and skills:
        skills_line = ", ".join(latex_escape(sk) for sk in skills if sk)
        skills_rows.append(f"\\textbf{{Skills:}} & {skills_line} \\\\")

    if skills_rows:
        skills_section = (
            "\\section*{Skills}\n"
            "\\begin{tabular}{@{}p{2.2cm}p{13cm}@{}}\n"
            + "\n".join(skills_rows)
            + "\n\\end{tabular}"
        )
    else:
        skills_section = ""

    # ── Header lines ───────────────────────────────────────────────────────────
    contact_items = []
    if profile.get("email"):
        contact_items.append(
            f"\\href{{mailto:{latex_escape(profile['email'])}}}"
            f"{{{latex_escape(profile['email'])}}}"
        )
    if profile.get("phone"):
        contact_items.append(latex_escape(profile["phone"]))
    if profile.get("location"):
        contact_items.append(latex_escape(profile["location"]))

    formatted_links_header = []
    for link in links:
        url_raw      = (link.get("url") or "").strip()
        display_raw  = (link.get("display") or link.get("url") or "").strip()
        link_type_raw = (link.get("type") or "").strip()
        if url_raw:
            label = latex_escape(display_raw or url_raw)
            escaped_url = latex_escape(url_raw)
            entry = f"\\href{{{escaped_url}}}{{{label}}}"
            if link_type_raw:
                entry = f"{latex_escape(link_type_raw)}: {entry}"
            formatted_links_header.append(entry)

    header_line1 = " $\\cdot$ ".join(contact_items)
    header_line2 = " $\\cdot$ ".join(formatted_links_header)

    # ── Section strings ────────────────────────────────────────────────────────
    summary_section = f"\\section*{{Summary}}\n\\small {summary}\n" if summary else ""

    education_section = (
        "\\section*{Education}\n" + "".join(education_blocks)
    ) if education_blocks else ""

    experience_section = (
        "\\section*{Experience}\n" + "".join(experience_blocks)
    ) if experience_blocks else ""

    projects_section = (
        "\\section*{Projects}\n" + "".join(project_blocks)
    ) if project_blocks else ""

    extracurricular_section = (
        "\\section*{Extracurricular Activities \\& Leadership}\n"
        + "".join(extracurricular_blocks)
    ) if extracurricular_blocks else ""

    certifications_section = ""
    if certification_lines:
        certifications_section = (
            "\\section*{Certifications}\n\\begin{itemize}\n"
            + "\n".join(f"  \\item {line}" for line in certification_lines)
            + "\n\\end{itemize}\n"
        )

    awards_section = ""
    if award_lines:
        awards_section = (
            "\\section*{Awards \\& Achievements}\n\\begin{itemize}\n"
            + "\n".join(f"  \\item {line}" for line in award_lines)
            + "\n\\end{itemize}\n"
        )

    languages_section = (
        "\\section*{Languages}\n"
        + " $\\cdot$ ".join(language_lines) + "\n"
    ) if language_lines else ""

    # ── Assemble document ──────────────────────────────────────────────────────
    latex = f"""
\\documentclass[{font_size},a4paper]{{article}}
\\usepackage[top={top_margin},bottom={top_margin},left={side_margin},right={side_margin}]{{geometry}}
\\usepackage[hidelinks,colorlinks=false]{{hyperref}}
\\usepackage{{xcolor}}

% ── Section headings: bold small-caps + full-width rule (no titlesec needed)
\\makeatletter
\\renewcommand\\section{{\\@ifstar\\mcsect\\mcsect}}
\\newcommand\\mcsect[1]{{%
  \\vspace{{5pt}}%
  \\noindent{{\\large\\bfseries\\scshape #1}}%
  \\par\\vspace{{1pt}}\\noindent\\rule{{\\linewidth}}{{0.4pt}}\\vspace{{3pt}}%
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
  \\small {header_line1} \\\\[2pt]
  \\small {header_line2}
\\end{{center}}

\\vspace{{2pt}}

{summary_section}
{skills_section}
{education_section}
{experience_section}
{projects_section}
{extracurricular_section}
{certifications_section}
{awards_section}
{languages_section}

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
            text=True,
            timeout=60,
            env=_TEX_ENV,
        )

        if not os.path.exists(pdf_path):
            tail = (result.stdout or "")[-1500:]
            raise RuntimeError(f"pdflatex failed:\n{tail}")

        with open(pdf_path, "rb") as fh:
            return fh.read()
