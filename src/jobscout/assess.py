"""The assessor — score job postings against the profile + rubric.

Two tiers:
  - TRIAGE (batch): many JDs per request, coarse numeric scores over everything.
  - DEEP (single):  one JD per request, the full rubric verdict + reasoning.

Assessment level per posting: 0 none · 1 triaged · 2 deep.
Pluggable provider (config `assessor.provider`): openai_compat / claude / ollama.
See ASSESSOR.md.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from functools import lru_cache

import httpx

from . import db
from .config import AssessorConfig, Config
from .models import Assessment

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


class AssessError(RuntimeError):
    pass


# -- prompt inputs --------------------------------------------------------

_ANNOTATION_RE = re.compile(r"\s*\[(?:VERIFY|ADD|CONFLICT|CLARIFY|GAP)[:\]][^\]]*\]?", re.IGNORECASE)
_CUT_MARKERS = ("## Framing decisions", "## Notes for tailoring", "## Framing")
_PACK_DEEP_CHARS = 13000       # full profile for the one-JD deep pass
_PACK_TRIAGE_CHARS = 4500      # short profile for the batched coarse pass (fits free-tier limits)


def _condense(text: str) -> str:
    for marker in _CUT_MARKERS:
        i = text.find(marker)
        if i != -1:
            text = text[:i]
    text = _ANNOTATION_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@lru_cache(maxsize=4)
def _read_dir_files(dir_str: str, names: tuple[str, ...], condense: bool) -> str:
    from pathlib import Path

    d = Path(dir_str)
    parts = []
    for name in names:
        f = d / name
        if f.exists():
            body = _condense(f.read_text()) if condense else f.read_text().strip()
            parts.append(f"## {name}\n\n{body}")
    return "\n\n---\n\n".join(parts)


def build_profile_pack(cfg: Config, *, brief: bool = False) -> str:
    # brief mode: the two files that actually drive scoring, hard-capped
    names = ("target-areas.md", "evaluating-offers.md") if brief else tuple(_PROFILE_FILES)
    cap = _PACK_TRIAGE_CHARS if brief else _PACK_DEEP_CHARS
    pack = _read_dir_files(str(cfg.profile_dir), names, True)
    if not pack:
        raise AssessError(f"no profile files in {cfg.profile_dir} (want {names})")
    return pack[:cap] + ("\n\n[truncated]" if len(pack) > cap else "")


def load_rubric(cfg: Config) -> str:
    rubric = _read_dir_files(str(cfg.eval_dir), (_RUBRIC_FILE,), False)
    if not rubric:
        raise AssessError(f"{_RUBRIC_FILE} not found in {cfg.eval_dir}")
    return rubric


def _base_system(cfg: Config, *, brief: bool = False) -> str:
    rubric = load_rubric(cfg)
    if brief:
        rubric = rubric[:2600]        # scoring scales are near the top
    return (
        "You assess job postings for a specific candidate. Use the candidate profile and the "
        "scoring rubric below. Judge the *work* described, not the title.\n\n"
        f"Current job-search mode: {cfg.mode}\n\n"
        "# CANDIDATE PROFILE\n\n" + build_profile_pack(cfg, brief=brief) + "\n\n"
        "# SCORING RUBRIC\n\n" + rubric
    )


_SCORE_KEYS = """\
  "score_overall":   integer 0-100  — overall fit (weigh quality and chance),
  "score_chance":    integer 0-100  — odds of landing it given the profile + gaps,
  "score_quality":   integer 0-100  — how good the role is if landed (trajectory bar),
  "area":            "A" | "B" | "C" | "D",
  "verdict":         "pursue" | "maybe" | "skip",
  "comp_vs_baseline":"above" | "within" | "below" | "unknown\""""

_TRIAGE_SYSTEM_TAIL = (
    "\n\n# TASK\n\nYou will receive several job postings, numbered. Score each one. "
    'Return ONLY a JSON object: {"assessments": [ ... ]}, one entry per posting, each:\n{\n'
    '  "i": integer  — the posting number,\n' + _SCORE_KEYS + ',\n'
    '  "one_liner":  string  — one clause on the fit\n}\nNo prose.'
)

_DEEP_SYSTEM_TAIL = (
    "\n\n# TASK\n\nAssess the one posting below. Return ONLY a JSON object:\n{\n"
    + _SCORE_KEYS + ",\n"
    '  "area_reason":    string   — one clause: why this area, judging the work,\n'
    '  "target_quality": "high" | "med" | "low",\n'
    '  "chance":         "high" | "med" | "low",\n'
    '  "overall":        string   — one line capturing the reasoning,\n'
    '  "gaps_hit":       string[] — JD requirements the candidate lacks,\n'
    '  "keep_de_titles": boolean  — true if the JD values Data-Engineer experience positively\n'
    "}\nNo prose."
)


def _jd_block(title: str, company: str, location: str, jd: str, cap: int) -> str:
    jd = jd.strip()
    if len(jd) > cap:
        jd = jd[:cap] + " […]"
    return f"Title: {title}\nCompany: {company}\nLocation: {location}\n\n{jd}"


# -- provider call ------------------------------------------------------


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        text = text[4:] if text[:4].lower() == "json" else text
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise AssessError(f"no JSON object in model output: {text[:200]}")
    return json.loads(text[s : e + 1])


def _chat(ac: AssessorConfig, system: str, user: str) -> dict:
    if ac.provider == "claude":
        return _chat_claude(ac, system, user)

    if not ac.base_url:
        raise AssessError("assessor.base_url is required for provider openai_compat")
    headers = {"Content-Type": "application/json"}
    if ac.api_key:
        headers["Authorization"] = f"Bearer {ac.api_key}"
    elif ac.provider != "ollama":
        raise AssessError("no API key — set ASSESSOR_API_KEY in .env (free at console.groq.com)")
    body = {
        "model": ac.model,
        "temperature": ac.temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    url = ac.base_url.rstrip("/") + "/chat/completions"
    for attempt in range(4):
        r = httpx.post(url, headers=headers, json=body, timeout=120.0)
        if r.status_code == 429:
            wait = float(r.headers.get("retry-after", 0)) or min(15 * (attempt + 1), 60)
            time.sleep(wait)
            continue
        if r.status_code == 413:
            raise AssessError(
                "request too large for this tier — lower assessor.batch_size / triage_jd_chars"
            )
        r.raise_for_status()
        return _extract_json(r.json()["choices"][0]["message"]["content"])
    raise AssessError("rate limited after retries — wait and retry")


def _chat_claude(ac: AssessorConfig, system: str, user: str) -> dict:
    try:
        import anthropic
    except ImportError as e:
        raise AssessError('provider "claude" needs: pip install -e ".[claude]"') from e
    if not ac.api_key:
        raise AssessError("no ANTHROPIC_API_KEY in .env")
    client = anthropic.Anthropic(api_key=ac.api_key)
    msg = client.messages.create(
        model=ac.model,
        max_tokens=2048,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user + "\n\nReturn only the JSON object."}],
    )
    return _extract_json("".join(b.text for b in msg.content if b.type == "text"))


# -- validation --------------------------------------------------------


def _score(d: dict, key: str) -> int:
    try:
        n = round(float(d.get(key)))
    except (TypeError, ValueError) as e:
        raise AssessError(f"{key!r}={d.get(key)!r} is not a number") from e
    return max(0, min(100, int(n)))


def _enum(d: dict, key: str, allowed: set[str]) -> str:
    v = d.get(key)
    if v not in allowed:
        raise AssessError(f"{key!r}={v!r} not in {sorted(allowed)}")
    return v


def _common(d: dict) -> dict:
    return {
        "score_overall": _score(d, "score_overall"),
        "score_chance": _score(d, "score_chance"),
        "score_quality": _score(d, "score_quality"),
        "area": _enum(d, "area", _AREAS),
        "verdict": _enum(d, "verdict", _VERDICTS),
        "comp_vs_baseline": _enum(d, "comp_vs_baseline", _COMP),
    }


# -- tier 1: triage (batch) -------------------------------------------


def triage_batch(cfg: Config, items: list[dict]) -> dict[int, dict]:
    """items: [{id, title, company, location, description}]. Returns {id: verdict-dict}."""
    ac = cfg.assessor
    system = _base_system(cfg, brief=True) + _TRIAGE_SYSTEM_TAIL
    blocks = []
    for n, it in enumerate(items, 1):
        blocks.append(
            f"### POSTING {n}\n"
            + _jd_block(it["title"], it["company"], it["location"], it["description"], ac.triage_jd_chars)
        )
    user = "\n\n".join(blocks)

    raw = _chat(ac, system, user)
    rows = raw.get("assessments") or raw.get("results") or []
    if not isinstance(rows, list):
        raise AssessError("triage response missing an assessments array")
    by_n = {}
    for entry in rows:
        try:
            n = int(entry["i"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= n <= len(items):
            c = _common(entry)
            c["overall"] = str(entry.get("one_liner", "")).strip()
            by_n[items[n - 1]["id"]] = c
    missing = [it["id"] for it in items if it["id"] not in by_n]
    if missing:
        raise AssessError(f"triage batch omitted {len(missing)}/{len(items)} postings")
    return by_n


# -- tier 2: deep (single) -------------------------------------------


_JD_MAX = 9000


def deep_one(cfg: Config, *, title: str, company: str, location: str, description: str) -> dict:
    ac = cfg.assessor
    system = _base_system(cfg) + _DEEP_SYSTEM_TAIL
    user = "### POSTING\n" + _jd_block(title, company, location, description, _JD_MAX)
    last = None
    for _ in range(2):
        try:
            d = _chat(ac, system, user)
            out = _common(d)
            out.update(
                area_reason=str(d.get("area_reason", "")).strip(),
                target_quality=_enum(d, "target_quality", _LMH),
                chance=_enum(d, "chance", _LMH),
                overall=str(d.get("overall", "")).strip(),
                gaps_hit=[str(g) for g in (d.get("gaps_hit") or [])],
                keep_de_titles=bool(d.get("keep_de_titles", False)),
            )
            return out
        except (AssessError, json.JSONDecodeError, KeyError) as e:
            last = e
            user += f"\n\nYour previous response was invalid ({e}). Return ONLY valid JSON."
    raise AssessError(f"deep assessment failed: {last}")


# -- orchestration ---------------------------------------------------


def _row_item(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"] or "",
        "company": row["company"] or "",
        "location": row["location"] or "",
        "description": row["description"] or "",
    }


def triage_pending(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    run_id: int | None = None,
    limit: int | None = None,
    progress=None,
    cancel=None,
) -> dict:
    """Tier 1 over level-0 postings (have a description, no assessment)."""
    if cfg.assessor is None:
        raise AssessError("no `assessor:` section in config.yaml")
    rows = db.postings_pending_assessment(conn, run_id=run_id, limit=limit)
    items = [_row_item(r) for r in rows]
    bs = max(1, cfg.assessor.batch_size)
    done = failed = 0
    for i in range(0, len(items), bs):
        if cancel and cancel.is_set():
            break
        if i:
            time.sleep(1.0)
        chunk = items[i : i + bs]
        try:
            verdicts = triage_batch(cfg, chunk)
            for pid, v in verdicts.items():
                db.store_assessment(
                    conn,
                    Assessment(posting_id=pid, method="batch", model=cfg.assessor.model_tag, **v),
                )
                done += 1
        except AssessError as e:
            failed += len(chunk)
            print(f"  ! triage chunk {i}-{i+len(chunk)} failed: {e}")
        if progress:
            progress(done, failed, len(items))
    return {"assessed": done, "failed": failed, "pending": len(items)}


def deep_top(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    limit: int = 15,
    progress=None,
    cancel=None,
) -> dict:
    """Tier 2 on the top `limit` level-1 postings by score_overall."""
    if cfg.assessor is None:
        raise AssessError("no `assessor:` section in config.yaml")
    rows = db.postings_for_deep(conn, limit=limit)
    done = failed = 0
    for i, row in enumerate(rows):
        if cancel and cancel.is_set():
            break
        if i:
            time.sleep(1.0)
        try:
            _deep_store(conn, cfg, row)
            done += 1
        except AssessError as e:
            failed += 1
            print(f"  ! deep #{row['id']} failed: {e}")
        if progress:
            progress(done, failed, len(rows))
    return {"assessed": done, "failed": failed, "pending": len(rows)}


def _deep_store(conn, cfg, row) -> Assessment:
    v = deep_one(
        cfg,
        title=row["title"] or "",
        company=row["company"] or "",
        location=row["location"] or "",
        description=row["description"] or "",
    )
    a = Assessment(posting_id=row["id"], method="single", model=cfg.assessor.model_tag, **v)
    db.store_assessment(conn, a)
    return a


def assess_posting(conn: sqlite3.Connection, cfg: Config, posting_id: int) -> Assessment:
    """Deep-assess one posting (the per-row 'correct the number' button)."""
    if cfg.assessor is None:
        raise AssessError("no `assessor:` section in config.yaml")
    row = db.get_posting(conn, posting_id)
    if not row:
        raise AssessError(f"no posting {posting_id}")
    if not (row["description"] or "").strip():
        raise AssessError("posting has no description — fetch it first")
    return _deep_store(conn, cfg, row)
