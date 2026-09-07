"""Generate a tailored resume for one posting.

Follows the private career repo: `experience-raw.md` is the source of truth,
`resumes/README.md` is the workflow, `resumes/bases/*.md` are the base resumes.
Output is markdown: the tailored resume + a "tailoring notes" block with
verify-before-sending flags.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import db
from .assess import AssessError, _chat_raw, _condense
from .config import Config

_NOTES_MARKER = "<!-- TAILORING NOTES -->"

_RULES = (
    "Tailoring conventions: pick a base resume by area (A = SWE/data-platform, B = AI engineer). "
    "Reorder and reweight bullets toward the JD; mirror the JD's wording where honest; adjust the "
    "headline + summary; add one or two targeted lines from the experience file. One page. Never "
    "fabricate. Keep bullets tech-agnostic where the experience file flags a framing risk. Where "
    "the JD values Data-Engineer experience positively, keep the literal 'Data Engineer' titles."
)


def _read(path: Path, cap: int = 0, condense: bool = False) -> str:
    if not path.exists():
        return ""
    t = path.read_text().strip()
    if condense:
        t = _condense(t)
    return t[:cap] if cap else t


def _pick_base(profile_dir: Path, area: str | None) -> tuple[str, str]:
    """Return (base_label, base_text). B for AI-engineer roles if a B base exists, else A."""
    bases = profile_dir / "resumes" / "bases"
    want = "B" if area == "B" else "A"
    for f in sorted(bases.glob("*.md")) if bases.exists() else []:
        name = f.name.lower()
        if want == "B" and ("-b-" in name or name.startswith("resume-b")):
            return "B", f.read_text().strip()
    for f in sorted(bases.glob("*.md")) if bases.exists() else []:
        name = f.name.lower()
        if "-a-" in name or name.startswith("resume-a"):
            return "A", f.read_text().strip()
    return "A", ""


def _system(cfg: Config, base_text: str) -> str:
    d = cfg.profile_dir
    experience = _read(d / "experience-raw.md", cap=11000, condense=True)
    gaps = _read(d / "gaps.md", cap=1500, condense=True)
    base = (base_text or "(no base found; build from EXPERIENCE)").split("— VERIFY BEFORE SENDING —")[0][:5500]
    return (
        "You tailor a candidate's resume to one job posting. Hard rules:\n"
        "- The SOURCE OF TRUTH is the experience file below. Never invent a role, skill, number, "
        "or claim not supported there.\n"
        "- One page of content. Plain markdown. No em-dashes in prose.\n\n"
        + _RULES + "\n\n"
        "# EXPERIENCE (source of truth)\n\n" + experience + "\n\n"
        "# SKILL GAPS TO BE AWARE OF\n\n" + gaps + "\n\n"
        "# BASE RESUME TO TAILOR FROM\n\n" + base + "\n\n"
        "# OUTPUT FORMAT\n\n"
        "Return the tailored resume as markdown. Then a line with exactly `" + _NOTES_MARKER + "`. "
        "Then a short section: bullets reordered / language mirrored / lines added, and a "
        "`Verify before sending` list of anything the candidate must confirm (unverified claims, "
        "titles, numbers)."
    )


def _user(row: sqlite3.Row, assessment: sqlite3.Row | None) -> str:
    jd = (row["description"] or "").strip()[:5000]
    parts = [
        (
            f"JOB POSTING\nCompany: {row['company']}\nTitle: {row['title']}\n"
            f"Location: {row['location']}\nURL: {row['url']}\n\n{jd}"
        )
    ]
    if assessment and assessment["verdict"]:
        gaps = assessment["gaps_hit"]
        try:
            gaps = ", ".join(json.loads(gaps)) if gaps else ""
        except (TypeError, json.JSONDecodeError):
            gaps = str(gaps or "")
        parts.append(
            "\n\nASSESSMENT (for context — tailor toward the strengths, be ready for the gaps):\n"
            f"area {assessment['area']} · verdict {assessment['verdict']} · "
            f"comp {assessment['comp_vs_baseline']}\n"
            f"gaps this JD hits: {gaps or 'none noted'}\n"
            f"keep the literal 'Data Engineer' titles: {'yes' if assessment['keep_de_titles'] else 'no'}"
        )
    return "".join(parts)


def generate_resume(conn: sqlite3.Connection, cfg: Config, posting_id: int) -> dict:
    if cfg.assessor is None:
        raise AssessError("no `assessor:` section in config.yaml")
    row = db.get_posting(conn, posting_id)
    if not row:
        raise AssessError(f"no posting {posting_id}")
    if not (row["description"] or "").strip():
        raise AssessError("posting has no description — fetch it first")

    area = row["area"]  # from the assessment join in _POSTING_SELECT (may be None)
    base_label, base_text = _pick_base(cfg.profile_dir, area)
    system = _system(cfg, base_text)
    user = _user(row, row)

    text = _chat_raw(cfg.assessor, system, user, json_mode=False).strip()
    if _NOTES_MARKER in text:
        content, notes = text.split(_NOTES_MARKER, 1)
    else:
        content, notes = text, ""

    rid = db.store_resume(
        conn, posting_id,
        base=base_label, content=content.strip(), notes=notes.strip(),
        model=cfg.assessor.model_tag,
    )
    return {"id": rid, "base": base_label, "content": content.strip(), "notes": notes.strip()}
