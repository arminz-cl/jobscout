"""The assessor — score one job posting against the profile + rubric.

Pluggable LLM provider (config `assessor.provider`): `openai_compat` (Groq / Gemini /
OpenRouter / Ollama), `claude` (Anthropic SDK). One JD per call, structured JSON out.
See ASSESSOR.md for the design.
"""

from __future__ import annotations

import json
import sqlite3
from functools import lru_cache

import httpx

from . import db
from .config import AssessorConfig, Config
from .models import Assessment

# Profile-pack sources, in order. Missing files are skipped.
_PROFILE_FILES = [
    "experience-raw.md",
    "target-areas.md",
    "gaps.md",
    "evaluating-offers.md",
    "intention-modes.md",
]
_RUBRIC_FILE = "assessment-guide.md"

_AREAS = {"A", "B", "C", "D"}
_LMH = {"high", "med", "low"}
_VERDICTS = {"pursue", "maybe", "skip"}
_COMP = {"above", "within", "below", "unknown"}

SCHEMA_DESCRIPTION = """\
Return ONLY a JSON object, no prose, with exactly these keys:
{
  "area":             "A" | "B" | "C" | "D",
  "area_reason":      string   — one clause: why this area, judging the work not the title,
  "target_quality":   "high" | "med" | "low",
  "chance":           "high" | "med" | "low",
  "verdict":          "pursue" | "maybe" | "skip",
  "overall":          string   — one line capturing the reasoning,
  "gaps_hit":         string[] — JD requirements the candidate lacks (e.g. "kubernetes"),
  "comp_vs_baseline": "above" | "within" | "below" | "unknown",
  "keep_de_titles":   boolean  — true if the JD values Data-Engineer experience positively
}
"""


class AssessError(RuntimeError):
    pass


# -- prompt inputs ----------------------------------------------------------


@lru_cache(maxsize=4)
def _read_dir_files(dir_str: str, names: tuple[str, ...]) -> str:
    from pathlib import Path

    d = Path(dir_str)
    parts = []
    for name in names:
        f = d / name
        if f.exists():
            parts.append(f"## {name}\n\n{f.read_text().strip()}")
    return "\n\n---\n\n".join(parts)


def build_profile_pack(cfg: Config) -> str:
    pack = _read_dir_files(str(cfg.profile_dir), tuple(_PROFILE_FILES))
    if not pack:
        raise AssessError(
            f"no profile files found in {cfg.profile_dir} (looked for {_PROFILE_FILES})"
        )
    return pack


def load_rubric(cfg: Config) -> str:
    rubric = _read_dir_files(str(cfg.eval_dir), (_RUBRIC_FILE,))
    if not rubric:
        raise AssessError(f"{_RUBRIC_FILE} not found in {cfg.eval_dir}")
    return rubric


def _system_prompt(cfg: Config) -> str:
    return (
        "You assess job postings for a specific candidate. Use the candidate profile and the "
        "scoring rubric below. Judge the *work* described in the JD, not the title.\n\n"
        f"Current job-search mode: {cfg.mode}\n\n"
        "# CANDIDATE PROFILE\n\n" + build_profile_pack(cfg) + "\n\n"
        "# SCORING RUBRIC\n\n" + load_rubric(cfg) + "\n\n"
        "# OUTPUT\n\n" + SCHEMA_DESCRIPTION
    )


def _user_prompt(title: str, company: str, location: str, description: str) -> str:
    return (
        f"JOB POSTING\nTitle: {title}\nCompany: {company}\nLocation: {location}\n\n"
        f"{description.strip()}"
    )


# -- validation -----------------------------------------------------------


def _validate(d: dict) -> dict:
    def need(key, allowed):
        v = d.get(key)
        if v not in allowed:
            raise AssessError(f"field {key!r}={v!r} not in {sorted(allowed)}")
        return v

    need("area", _AREAS)
    need("target_quality", _LMH)
    need("chance", _LMH)
    need("verdict", _VERDICTS)
    need("comp_vs_baseline", _COMP)
    gaps = d.get("gaps_hit") or []
    if not isinstance(gaps, list):
        raise AssessError("gaps_hit must be a list")
    return {
        "area": d["area"],
        "area_reason": str(d.get("area_reason", "")).strip(),
        "target_quality": d["target_quality"],
        "chance": d["chance"],
        "verdict": d["verdict"],
        "overall": str(d.get("overall", "")).strip(),
        "gaps_hit": [str(g) for g in gaps],
        "comp_vs_baseline": d["comp_vs_baseline"],
        "keep_de_titles": bool(d.get("keep_de_titles", False)),
    }


# -- providers ----------------------------------------------------------


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        text = text[4:] if text.lower().startswith("json") else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise AssessError(f"no JSON object in model output: {text[:200]}")
    return json.loads(text[start : end + 1])


def _call_openai_compat(ac: AssessorConfig, system: str, user: str) -> dict:
    if not ac.base_url:
        raise AssessError("assessor.base_url is required for provider openai_compat")
    headers = {"Content-Type": "application/json"}
    if ac.api_key:
        headers["Authorization"] = f"Bearer {ac.api_key}"
    elif ac.provider != "ollama":
        raise AssessError(
            "no API key — set ASSESSOR_API_KEY (or GROQ_API_KEY) in .env "
            "(free key at console.groq.com)"
        )
    body = {
        "model": ac.model,
        "temperature": ac.temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    r = httpx.post(
        ac.base_url.rstrip("/") + "/chat/completions",
        headers=headers,
        json=body,
        timeout=60.0,
    )
    if r.status_code == 429:
        raise AssessError("rate limited by the assessor provider — wait and retry")
    r.raise_for_status()
    return _extract_json(r.json()["choices"][0]["message"]["content"])


def _call_claude(ac: AssessorConfig, system: str, user: str) -> dict:
    try:
        import anthropic
    except ImportError as e:
        raise AssessError('provider "claude" needs: pip install -e ".[claude]"') from e
    if not ac.api_key:
        raise AssessError("no ANTHROPIC_API_KEY in .env")
    client = anthropic.Anthropic(api_key=ac.api_key)
    msg = client.messages.create(
        model=ac.model,
        max_tokens=1024,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user + "\n\nReturn only the JSON object."}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    return _extract_json(text)


def _dispatch(ac: AssessorConfig, system: str, user: str) -> dict:
    if ac.provider == "claude":
        return _call_claude(ac, system, user)
    return _call_openai_compat(ac, system, user)  # openai_compat + ollama


# -- public API ----------------------------------------------------------


def assess_jd(cfg: Config, *, title: str, company: str, location: str, description: str) -> dict:
    """One assessment. Returns the validated verdict dict (no DB write)."""
    if cfg.assessor is None:
        raise AssessError("no `assessor:` section in config.yaml")
    system = _system_prompt(cfg)
    user = _user_prompt(title, company, location, description)
    last_err = None
    for attempt in range(2):
        try:
            raw = _dispatch(cfg.assessor, system, user)
            return _validate(raw)
        except (AssessError, json.JSONDecodeError, KeyError) as e:
            last_err = e
            user = (
                _user_prompt(title, company, location, description)
                + f"\n\nYour previous response was invalid ({e}). Return ONLY a valid JSON object."
            )
    raise AssessError(f"assessment failed after 2 attempts: {last_err}")


def assess_posting(conn: sqlite3.Connection, cfg: Config, posting_id: int) -> Assessment:
    row = db.get_posting(conn, posting_id)
    if not row:
        raise AssessError(f"no posting {posting_id}")
    if not (row["description"] or "").strip():
        raise AssessError("posting has no description — fetch it first")
    verdict = assess_jd(
        cfg,
        title=row["title"] or "",
        company=row["company"] or "",
        location=row["location"] or "",
        description=row["description"],
    )
    a = Assessment(posting_id=posting_id, model=cfg.assessor.model_tag, **verdict)
    db.store_assessment(conn, a)
    return a


def assess_pending(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    run_id: int | None = None,
    limit: int | None = None,
    progress=None,
    cancel=None,
) -> dict:
    rows = db.postings_pending_assessment(conn, run_id=run_id, limit=limit)
    done, failed = 0, 0
    for row in rows:
        if cancel and cancel.is_set():
            break
        try:
            assess_posting(conn, cfg, row["id"])
            done += 1
        except AssessError as e:
            failed += 1
            print(f"  ! assess #{row['id']} failed: {e}")
        if progress:
            progress(done, failed, len(rows))
    return {"assessed": done, "failed": failed, "pending": len(rows)}
