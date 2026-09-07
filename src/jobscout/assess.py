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

# optional live-status hook — the runner sets this so the UI can show fine-grained state
# ("calling groq…", "rate-limited, waiting 21s", "idle").
STATUS = None  # type: ignore[assignment]


def _status(msg: str) -> None:
    if STATUS is not None:
        try:
            STATUS(msg)
        except Exception:  # noqa: BLE001, S110 - status must never break a run
            pass  # nosec


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

_DEEP_TASK = """\

# TASK

Assess the one posting below. Work in tiers, not fine-grained numbers.

## target_quality — how good the role is IF landed (the trajectory bar)
- high : SWE / AI-engineer title (or unambiguously SWE-on-data); real systems; genuine scale
        or complexity; level + label point up.
- med  : solid but with a compromise — internal tooling not product, unglamorous domain, so-so
        level, or a DE title that is really engineering.
- low  : data-mover DE title; below comp baseline; outside the engineering org; product pivot
        away from the goal; no trajectory fix.

## chance — odds of landing it, given the candidate's profile + gaps
- high : meets the stated bar; few/no hard gaps; strong keyword overlap.
- med  : meets most; 1-2 real gaps that prep or a portfolio project can close; decent overlap.
- low  : misses a hard requirement (years, a core skill) or several gaps; still plausible only
        with a strong tailored pitch or a warm intro.

## verdict
- pursue = area A or B AND target_quality >= med AND chance is at least low-but-real.
- area C -> pursue only if target_quality is high and it is customer-facing.
- area D -> skip unless the work is genuinely A/B in disguise.
- comp clearly below baseline AND no trajectory fix -> skip regardless.
- maybe = borderline on one axis.
- skip = fails the above, or a hard blocker (10+ yrs required, wrong domain, sub-floor comp).
- In "casual" mode the candidate is NOT job-hunting under pressure: weight target_quality
  heavily. A high-chance role that does not clear the trajectory bar is a skip, not a maybe.

## calibration examples (JD summary -> correct call)
- "Backend SWE, Data Layer" at a remote enterprise co; Postgres/Elasticsearch/backend match,
   good comp, SWE title -> area A, quality high, chance med, PURSUE. Best odds-to-quality.
- "Senior/Staff Data Platform Engineer"; Spark-on-K8s / Flink / Delta / Terraform; the
   platform-infra bar is real and the candidate has IaC/K8s gaps -> area A, quality high,
   chance low, PURSUE. A stretch worth taking.
- "Data Engineer" at a big marketplace; Java/Scala/Flink/Kafka/Databricks all required and
   unheld; DE title does not fix the label -> area D/A, quality med, chance med, MAYBE.
- "Applied AI Engineer, Business Solutions"; comp well below the baseline; sits in Revenue Ops
   not Engineering -> area B, quality low, chance med, SKIP. Trajectory red flag.
- "Senior Applied AI Engineer, Wealth"; requires 10+ yrs AI/ML + financial-services + a
   specific agent stack -> area B, quality med, chance very-low, SKIP. Out of range.

Return ONLY a JSON object:
{
  "area":            "A" | "B" | "C" | "D",
  "area_reason":     string   — one clause: why this area, judging the work not the title,
  "target_quality":  "high" | "med" | "low",
  "chance":          "high" | "med" | "low",
  "verdict":         "pursue" | "maybe" | "skip",
  "comp_vs_baseline":"above" | "within" | "below" | "unknown",
  "overall":         string   — one line, specific to THIS role (name the match and the gap),
  "gaps_hit":        string[] — JD requirements the candidate lacks,
  "keep_de_titles":  boolean  — true if the JD values Data-Engineer experience positively
}
No prose."""

_DEEP_SYSTEM_TAIL = _DEEP_TASK
_TIER_SCORE = {"high": 85, "med": 60, "low": 35}

# Share of the overall score driven by target_quality (the rest is chance).
# Casual = not urgent -> quality of the role dominates; the busier the search
# mode, the more the odds of landing it matter. Override with assessor.quality_weight.
_QUALITY_WEIGHT_BY_MODE = {"casual": 0.70, "active": 0.50, "urgent": 0.35}
_DEFAULT_QUALITY_WEIGHT = 0.45


def _quality_weight(cfg: Config) -> float:
    w = cfg.assessor.quality_weight
    if w is not None:
        return max(0.0, min(1.0, w))
    return _QUALITY_WEIGHT_BY_MODE.get(cfg.mode, _DEFAULT_QUALITY_WEIGHT)


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
    return _extract_json(_chat_raw(ac, system, user, json_mode=True))


def _chat_raw(ac: AssessorConfig, system: str, user: str, *, json_mode: bool) -> str:
    """Return the model's message content as text. json_mode adds response_format."""
    if ac.provider == "claude":
        return _chat_claude_raw(ac, system, user)

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
    }
    if "gpt-oss" in ac.model or "deepseek" in ac.model or "qwen" in ac.model:
        # reasoning models: minimal thinking — assessment is structured judgement, not a puzzle
        body["reasoning_effort"] = "low"
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    url = ac.base_url.rstrip("/") + "/chat/completions"
    host = "groq" if "groq" in (ac.base_url or "") else "provider"
    for attempt in range(4):
        _status(f"calling {host} ({ac.model})")
        r = httpx.post(url, headers=headers, json=body, timeout=180.0)
        if r.status_code == 429:
            wait = float(r.headers.get("retry-after", 0)) or min(15 * (attempt + 1), 60)
            _status(f"{host} rate-limited · next call in {round(wait)}s ({attempt + 1}/4)")
            time.sleep(wait)
            continue
        if r.status_code == 413:
            raise AssessError("request too large for this tier — lower batch_size / triage_jd_chars")
        r.raise_for_status()
        _status("parsing response")
        return r.json()["choices"][0]["message"]["content"]
    raise AssessError("rate limited after retries — wait and retry")


def _chat_claude(ac: AssessorConfig, system: str, user: str) -> dict:
    return _extract_json(_chat_claude_raw(ac, system, user))


def _chat_claude_raw(ac: AssessorConfig, system: str, user: str) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise AssessError('provider "claude" needs: pip install -e ".[claude]"') from e
    if not ac.api_key:
        raise AssessError("no ANTHROPIC_API_KEY in .env")
    client = anthropic.Anthropic(api_key=ac.api_key)
    msg = client.messages.create(
        model=ac.model,
        max_tokens=4096,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


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


def _common(d: dict, qw: float) -> dict:
    q_s, c_s = _score(d, "score_quality"), _score(d, "score_chance")
    return {
        # re-derive overall from the same quality/chance weighting deep uses, so the
        # triage ranking that feeds deep is already quality-led in casual mode
        "score_overall": round(qw * q_s + (1 - qw) * c_s),
        "score_chance": c_s,
        "score_quality": q_s,
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
    qw = _quality_weight(cfg)
    by_n = {}
    for entry in rows:
        try:
            n = int(entry["i"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= n <= len(items):
            c = _common(entry, qw)
            c["overall"] = str(entry.get("one_liner", "")).strip()
            by_n[items[n - 1]["id"]] = c
    missing = [it["id"] for it in items if it["id"] not in by_n]
    if missing:
        raise AssessError(f"triage batch omitted {len(missing)}/{len(items)} postings")
    return by_n


# -- tier 2: deep (single) -------------------------------------------


_JD_MAX = 9000


def deep_one(cfg: Config, *, title: str, company: str, location: str, description: str,
             company_rating: int | None = None) -> dict:
    ac = cfg.assessor
    system = _base_system(cfg) + _DEEP_SYSTEM_TAIL
    ctx = f"### POSTING\nTitle: {title}\nCompany: {company}"
    if company_rating:
        ctx += f"\nEmployer tech/prestige rating (manual, 1-5, higher is a stronger eng org): {company_rating}"
    ctx += f"\nLocation: {location}\n\n{description.strip()[:_JD_MAX]}"
    user = ctx
    last = None
    for _ in range(2):
        try:
            d = _chat(ac, system, user)
            area = _enum(d, "area", _AREAS)
            quality = _enum(d, "target_quality", _LMH)
            chance = _enum(d, "chance", _LMH)
            verdict = _enum(d, "verdict", _VERDICTS)
            q_s, c_s = _TIER_SCORE[quality], _TIER_SCORE[chance]
            # overall score: quality x chance blend (weight from search mode), nudged by verdict
            qw = _quality_weight(cfg)
            overall_s = round(qw * q_s + (1 - qw) * c_s)
            overall_s += {"pursue": 6, "maybe": 0, "skip": -8}[verdict]
            return {
                "area": area,
                "area_reason": str(d.get("area_reason", "")).strip(),
                "target_quality": quality,
                "chance": chance,
                "verdict": verdict,
                "comp_vs_baseline": _enum(d, "comp_vs_baseline", _COMP),
                "overall": str(d.get("overall", "")).strip(),
                "gaps_hit": [str(g) for g in (d.get("gaps_hit") or [])],
                "keep_de_titles": bool(d.get("keep_de_titles", False)),
                "score_quality": q_s,
                "score_chance": c_s,
                "score_overall": max(0, min(100, overall_s)),
            }
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
    groups: list[str] | None = None,
    limit: int | None = None,
    progress=None,
    cancel=None,
) -> dict:
    """Tier 1 over level-0 postings (have a description, no assessment)."""
    if cfg.assessor is None:
        raise AssessError("no `assessor:` section in config.yaml")
    rows = db.postings_pending_assessment(conn, run_id=run_id, groups=groups, limit=limit)
    items = [_row_item(r) for r in rows]
    bs = max(1, cfg.assessor.batch_size)
    done = failed = 0
    n_chunks = (len(items) + bs - 1) // bs
    for i in range(0, len(items), bs):
        if cancel and cancel.is_set():
            _status("stopped")
            break
        if i:
            time.sleep(1.0)
        chunk = items[i : i + bs]
        _status(f"triage batch {i // bs + 1}/{n_chunks} ({len(chunk)} postings)")
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
    balance_groups: list[str] | None = None,
    progress=None,
    cancel=None,
) -> dict:
    """Tier 2 on the top `limit` level-1 postings by score_overall.
    `balance_groups` interleaves the ranking across those groups."""
    if cfg.assessor is None:
        raise AssessError("no `assessor:` section in config.yaml")
    rows = db.postings_for_deep(conn, limit=limit, balance_groups=balance_groups)
    done = failed = 0
    for i, row in enumerate(rows):
        if cancel and cancel.is_set():
            _status("stopped")
            break
        if i:
            time.sleep(1.0)
        _status(f"deep {i + 1}/{len(rows)} — {row['company']}")
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
    crow = conn.execute(
        "SELECT rating FROM companies WHERE name = ?", (row["company"],)
    ).fetchone()
    v = deep_one(
        cfg,
        title=row["title"] or "",
        company=row["company"] or "",
        location=row["location"] or "",
        description=row["description"] or "",
        company_rating=(crow["rating"] if crow and crow["rating"] else None),
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
