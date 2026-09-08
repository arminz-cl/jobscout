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
from .assess import AssessError, _chat_raw, _condense, _extract_json, chat_turns
from .config import REPO_ROOT, Config, ResumeConfig

_NOTES_MARKER = "<!-- TAILORING NOTES -->"
_PRIVATE_FILE = REPO_ROOT / "resume.private.md"

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


# ======================================================================
# Interactive resume builder — a chat loop that surfaces true material to
# close the gaps a JD hits, maintaining a facts ledger + a live draft.
# ======================================================================

_PRIVATE_DEFAULT = "(no resume.private.md — using only experience-raw.md as the source of truth)"

_BUILDER_RULES = """\
You are a resume-tailoring partner working WITH the candidate, one job at a time.

Your goal: produce a one-page resume tuned to this posting by surfacing material
the candidate genuinely has but hasn't written down, and by reframing real work
toward what this role wants.

Hard rules — never break these:
- The candidate's experience brief + their private notes are the ONLY source of truth.
- Never invent a role, project, employer, metric, credential, or skill. If something
  isn't supported by what the candidate has told you, you do not add it.
- You may: add skill keywords for tools they confirm they've actually used; mirror
  the JD's exact wording for work they've actually done; reorder / reweight / rephrase
  real bullets; make an under-described real responsibility explicit.
- If the candidate asks you to fabricate or exaggerate past what they can defend in
  an interview, refuse that specific edit and say why, then offer the honest version.
- One page. Plain markdown. No em-dashes in prose.

How you work each turn:
1. Read the facts ledger and current draft (given below as STATE SO FAR).
2. Ask 2-4 SPECIFIC questions targeting the still-open gaps — reference the JD
   requirement and what you already know. Prefer questions that can pull out a real
   metric, a real system name, or a real responsibility.
3. Fold anything the candidate just confirmed into the ledger.
4. Update the draft to reflect confirmed material. Keep unconfirmed gaps out.
5. Tell the candidate what changed and what's still open.

Return ONLY a JSON object:
{
  "reply":     string  — your message to the candidate: a short fit read and your
                          2-4 questions ONLY. Do NOT paste the resume or the full
                          gap list here — those are shown separately from resume_md
                          and open_gaps. Keep it under ~150 words.
  "ledger":    string  — the FULL updated facts ledger (markdown bullets; you rewrite it each turn),
  "resume_md": string  — the FULL current draft resume in markdown,
  "notes":     string  — running "verify before sending" list + what you changed this turn,
  "open_gaps": string[] — JD gaps still not addressed
}
No prose outside the JSON."""


def _read_private() -> str:
    if _PRIVATE_FILE.exists():
        t = _PRIVATE_FILE.read_text().strip()
        return t[:4000] if t else _PRIVATE_DEFAULT
    return _PRIVATE_DEFAULT


def _resume_provider(cfg: Config) -> ResumeConfig:
    rc = cfg.resume
    if rc is None:
        raise AssessError("no `assessor:` or `resume:` section in config.yaml")
    if not rc.api_key and rc.provider != "ollama":
        raise AssessError("no API key for the resume builder — set RESUME_API_KEY or ASSESSOR_API_KEY in .env")
    return rc


def build_profile_brief(cfg: Config) -> str:
    """One-time condense of the full work history into a tight factual brief, so
    every builder turn carries ~1.5k tokens of profile instead of ~4k."""
    rc = _resume_provider(cfg)
    d = cfg.profile_dir
    exp = _read(d / "experience-raw.md", condense=True)
    gaps = _read(d / "gaps.md", cap=1800, condense=True)
    system = (
        "Condense this candidate's work history into a tight factual brief for a "
        "resume-tailoring assistant. Keep: every role (company, title, dates), the "
        "systems/tools/skills genuinely demonstrated in each, and any scale or impact "
        "numbers. Add a short 'soft spots' list of honest weaknesses. Drop prose and "
        "opinions. Target 350-500 words. Plain text, no preamble."
    )
    user = f"WORK HISTORY\n\n{exp}\n\nKNOWN GAPS\n\n{gaps or '(none listed)'}"
    return chat_turns(rc, system, [{"role": "user", "content": user}], json_mode=False).strip()


def _builder_system(cfg: Config, session: sqlite3.Row, row: sqlite3.Row) -> str:
    jd = (row["description"] or "").strip()[:3500]
    gaps = row["gaps_hit"]
    try:
        gaps = ", ".join(json.loads(gaps)) if gaps else ""
    except (TypeError, json.JSONDecodeError):
        gaps = str(gaps or "")
    assess_line = ""
    if row["verdict"]:
        assess_line = (
            f"\nAssessment: area {row['area']} · verdict {row['verdict']} · "
            f"comp {row['comp_vs_baseline']} · keep literal 'Data Engineer' titles: "
            f"{'yes' if row['keep_de_titles'] else 'no'}"
        )
    return (
        _BUILDER_RULES + "\n\n"
        "# CANDIDATE EXPERIENCE BRIEF (source of truth)\n\n" + (session["profile_brief"] or "") + "\n\n"
        "# PRIVATE TAILORING NOTES (source of truth — never committed)\n\n" + _read_private() + "\n\n"
        "# TARGET POSTING\n\n"
        f"Company: {row['company']}\nTitle: {row['title']}\nLocation: {row['location']}\n"
        f"URL: {row['url']}{assess_line}\n"
        f"Gaps this JD hits (from the assessment): {gaps or 'none noted'}\n\n"
        f"JD (truncated):\n{jd}"
    )


def _state_turns(session: sqlite3.Row, recent: list[sqlite3.Row], new_user: str | None) -> list[dict]:
    ledger = session["ledger"] or "(empty — nothing confirmed yet)"
    draft = session["draft"] or "(no draft yet)"
    turns: list[dict] = [
        {
            "role": "user",
            "content": (
                "STATE SO FAR\n\n"
                f"## Facts ledger\n{ledger}\n\n"
                f"## Current draft\n{draft}\n\n"
                "Continue from here."
            ),
        },
        {"role": "assistant", "content": "Got the ledger and the draft. What's the update?"},
    ]
    for m in recent:
        turns.append({"role": m["role"], "content": m["content"]})
    if new_user is not None:
        turns.append({"role": "user", "content": new_user})
    return turns


def _parse_turn(text: str) -> dict:
    d = _extract_json(text)
    return {
        "reply": str(d.get("reply", "")).strip(),
        "ledger": str(d.get("ledger", "")).strip(),
        "resume_md": str(d.get("resume_md", "")).strip(),
        "notes": str(d.get("notes", "")).strip(),
        "open_gaps": [str(g) for g in (d.get("open_gaps") or [])],
    }


def _session_payload(conn: sqlite3.Connection, session_id: int) -> dict:
    s = db.get_resume_session_by_id(conn, session_id)
    msgs = db.list_resume_messages(conn, session_id)
    return {
        "id": s["id"],
        "posting_id": s["posting_id"],
        "title": s["title"],
        "company": s["company"],
        "url": s["url"],
        "base": s["base"],
        "model": s["model"],
        "status": s["status"],
        "ledger": s["ledger"],
        "draft": s["draft"],
        "notes": s["notes"],
        "updated_at": s["updated_at"],
        "messages": [dict(m) for m in msgs],
    }


_OPENING_TRIGGER = (
    "Begin. Read the JD and my experience brief, give me a short honest read on the fit "
    "and the real gaps, then ask your first batch of questions. Include a first-pass draft "
    "built only from what's already confirmed."
)


def start_builder(conn: sqlite3.Connection, cfg: Config, posting_id: int) -> dict:
    existing = db.get_resume_session(conn, posting_id)
    if existing:
        return _session_payload(conn, existing["id"])

    rc = _resume_provider(cfg)
    row = db.get_posting(conn, posting_id)
    if not row:
        raise AssessError(f"no posting {posting_id}")
    if not (row["description"] or "").strip():
        raise AssessError("posting has no description — fetch it first")

    base_label, _ = _pick_base(cfg.profile_dir, row["area"])
    brief = build_profile_brief(cfg)
    sid = db.create_resume_session(
        conn, posting_id, base=base_label, model=rc.model_tag, profile_brief=brief
    )
    session = db.get_resume_session_by_id(conn, sid)
    system = _builder_system(cfg, session, row)
    turns = _state_turns(session, [], _OPENING_TRIGGER)
    out = _parse_turn(chat_turns(rc, system, turns, json_mode=True))

    db.add_resume_message(conn, sid, "user", _OPENING_TRIGGER)
    db.add_resume_message(conn, sid, "assistant", out["reply"])
    db.update_resume_session(
        conn, sid, ledger=out["ledger"], draft=out["resume_md"], notes=out["notes"]
    )
    return _session_payload(conn, sid)


def builder_reply(conn: sqlite3.Connection, cfg: Config, session_id: int, user_text: str) -> dict:
    rc = _resume_provider(cfg)
    session = db.get_resume_session_by_id(conn, session_id)
    if not session:
        raise AssessError(f"no resume session {session_id}")
    row = db.get_posting(conn, session["posting_id"])
    all_msgs = db.list_resume_messages(conn, session_id)
    recent = all_msgs[-6:]  # ~3 exchanges verbatim; older turns live in the ledger

    system = _builder_system(cfg, session, row)
    turns = _state_turns(session, recent, user_text.strip())
    out = _parse_turn(chat_turns(rc, system, turns, json_mode=True))

    db.add_resume_message(conn, session_id, "user", user_text.strip())
    db.add_resume_message(conn, session_id, "assistant", out["reply"])
    db.update_resume_session(
        conn, session_id,
        ledger=out["ledger"] or session["ledger"],
        draft=out["resume_md"] or session["draft"],
        notes=out["notes"] or session["notes"],
    )
    return _session_payload(conn, session_id)


def save_builder(conn: sqlite3.Connection, session_id: int) -> dict:
    session = db.get_resume_session_by_id(conn, session_id)
    if not session:
        raise AssessError(f"no resume session {session_id}")
    if not (session["draft"] or "").strip():
        raise AssessError("draft is empty — keep chatting until there's a resume to save")
    rid = db.store_resume(
        conn, session["posting_id"],
        base=session["base"] or "?", content=session["draft"].strip(),
        notes=(session["notes"] or "").strip(), model=session["model"] or "?",
    )
    db.update_resume_session(conn, session_id, status="saved")
    return {"id": rid, "posting_id": session["posting_id"]}
