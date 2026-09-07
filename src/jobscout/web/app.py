"""FastAPI backend. Localhost only. Serves the JSON API and the built React SPA."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import db
from ..config import ConfigError, load, resolve_window
from . import runner

app = FastAPI(title="jobscout", docs_url="/api/docs", openapi_url="/api/openapi.json")

# The Vite dev server runs on :5173 during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIST = Path(__file__).resolve().parent / "static"


def _conn():
    return db.connect(load().db_path)


def _row(r) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    if d.get("gaps_hit"):
        try:
            d["gaps_hit"] = json.loads(d["gaps_hit"])
        except (TypeError, json.JSONDecodeError):
            pass
    if "raw_json" in d:
        d.pop("raw_json", None)
    return d


# -- meta --------------------------------------------------------------------


@app.get("/api/config")
def get_config():
    try:
        cfg = load()
    except ConfigError as e:
        raise HTTPException(400, str(e)) from e
    conn = db.connect(cfg.db_path)
    last_run = db.last_successful_run_iso(conn, cfg.source)
    return {
        "mode": cfg.mode,
        "source": cfg.source,
        "model": cfg.model,
        "locations": [
            {"label": loc.label, "work_type": loc.work_type} for loc in cfg.locations
        ],
        "time_posted": cfg.time_posted,
        "resolved_window": resolve_window(cfg, None, last_run),
        "last_run": last_run,
        "max_results_per_query": cfg.max_results_per_query,
    }


@app.get("/api/query-groups")
def query_groups():
    try:
        cfg = load()
    except ConfigError as e:
        raise HTTPException(400, str(e)) from e
    return [
        {
            "name": g.name,
            "title": g.title,
            "summary": g.summary,
            "queries": list(g.queries),
            "active": g.name in cfg.mode_groups,
        }
        for g in cfg.query_groups.values()
    ]


# -- runs -------------------------------------------------------------------


class StartRun(BaseModel):
    groups: list[str] | None = None       # None -> the active-mode groups
    since: str | None = None              # "24h" | "7d" | "30d" | None (auto)
    phase: str = "full"                   # "cards" | "descriptions" | "full" | "assess"
    then_assess: bool = False             # chain assessment after a cards/full run
    assess_limit: int | None = None       # cap how many postings to score this run


@app.get("/api/runs")
def list_runs(limit: int = 20):
    conn = _conn()
    return [_row(r) for r in db.list_runs(conn, limit)]


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    conn = _conn()
    r = db.get_run(conn, run_id)
    if not r:
        raise HTTPException(404, "no such run")
    out = _row(r)
    out["is_current"] = bool(runner.current() and runner.current().run_id == run_id)
    out["busy"] = runner.is_busy()
    return out


@app.get("/api/runner")
def runner_state():
    cur = runner.current()
    act, act_at, wait_remaining = runner.activity()
    return {
        "busy": runner.is_busy(),
        "current_run_id": cur.run_id if cur else None,
        "kind": cur.kind if cur else None,
        "groups": cur.groups if cur else None,
        "error": cur.error if cur else None,
        "activity": act,
        "activity_age": round(time.time() - act_at, 1) if act_at else None,
        "wait_remaining": wait_remaining,
    }


@app.post("/api/runs")
def start_run(body: StartRun):
    try:
        run_id = runner.start_fetch(
            body.groups, body.since, phase=body.phase,
            then_assess=body.then_assess, assess_limit=body.assess_limit,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    except ConfigError as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id}


@app.get("/api/assessor")
def assessor_info():
    """Assessor config + how many postings are waiting to be scored."""
    try:
        cfg = load()
    except ConfigError as e:
        raise HTTPException(400, str(e)) from e
    conn = db.connect(cfg.db_path)
    counts = db.assessment_counts(conn)
    deep_ready = len(db.postings_for_deep(conn, limit=10000))
    ac = cfg.assessor
    return {
        "configured": ac is not None,
        "provider": ac.provider if ac else None,
        "model": ac.model if ac else None,
        "model_tag": ac.model_tag if ac else None,
        "has_key": bool(ac and ac.api_key) if ac else False,
        "pending": counts["level0"],       # level 0 -> triage
        "triaged": counts["level1"],
        "deep": counts["level2"],
        "deep_ready": deep_ready,           # level 1 -> can be deep-assessed
    }


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: int):
    if not runner.stop(run_id):
        raise HTTPException(409, "that run is not the one currently in progress")
    return {"ok": True, "note": "stop requested — the run halts at the next checkpoint"}


# -- postings --------------------------------------------------------------


@app.get("/api/postings")
def postings(
    query: str | None = None,
    group: str | None = None,
    run_id: int | None = None,
    company: str | None = None,
    verdict: str | None = None,
    assessed: bool | None = None,
    level: int | None = None,
    has_resume: bool | None = None,
    has_description: bool | None = None,
    seen: bool | None = None,
    starred: bool | None = None,
    status: str | None = None,
    search: str | None = None,
    order: str = "first_seen_at",
    limit: int = 100,
    offset: int = 0,
):
    conn = _conn()
    queries = None
    if group:
        try:
            g = load().query_groups.get(group)
            queries = list(g.queries) if g else None
        except ConfigError as e:
            raise HTTPException(400, str(e)) from e
    rows = db.list_postings(
        conn, query=query, group=group, queries=queries, run_id=run_id, company=company,
        verdict=verdict, assessed=assessed, level=level, has_resume=has_resume,
        has_description=has_description, seen=seen, starred=starred, status=status,
        search=search, order=order, limit=limit, offset=offset,
    )
    return {"count": len(rows), "results": [_row(r) for r in rows]}


@app.get("/api/postings/facets")
def posting_facets():
    """Counts to drive the filter toggles."""
    conn = _conn()
    try:
        cfg = load()
    except ConfigError as e:
        raise HTTPException(400, str(e)) from e
    total = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    with_desc = conn.execute(
        "SELECT COUNT(*) FROM postings WHERE description IS NOT NULL AND description != ''"
    ).fetchone()[0]
    assessed = conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]
    pending_assessment = len(db.postings_pending_assessment(conn))
    unseen = conn.execute("SELECT COUNT(*) FROM postings WHERE seen_at IS NULL").fetchone()[0]
    starred = conn.execute("SELECT COUNT(*) FROM postings WHERE starred = 1").fetchone()[0]
    resumes_n = conn.execute("SELECT COUNT(*) FROM resumes").fetchone()[0]
    by_group = []
    for g in cfg.query_groups.values():
        qs = list(g.queries)
        placeholders = ",".join("?" * len(qs))
        n = conn.execute(
            f"SELECT COUNT(*) FROM postings WHERE matched_group = ? "
            f"OR matched_query IN ({placeholders})",
            [g.name, *qs],
        ).fetchone()[0]
        by_group.append(
            {
                "name": g.name,
                "title": g.title,
                "summary": g.summary,
                "count": n,
                "active": g.name in cfg.mode_groups,
            }
        )
    verdicts = {
        r["verdict"]: r["n"]
        for r in conn.execute("SELECT verdict, COUNT(*) n FROM assessments GROUP BY verdict")
    }
    workplace = {
        (r["workplace_type"] or "unknown"): r["n"]
        for r in conn.execute(
            "SELECT workplace_type, COUNT(*) n FROM postings GROUP BY workplace_type"
        )
    }
    ac = db.assessment_counts(conn)
    return {
        "total": total,
        "with_description": with_desc,
        "without_description": total - with_desc,
        "assessed": assessed,
        "pending_assessment": pending_assessment,
        # counts match the `level=` filter: 0 = no assessment row at all
        "levels": {"0": total - ac["level1"] - ac["level2"], "1": ac["level1"], "2": ac["level2"]},
        "unseen": unseen,
        "starred": starred,
        "resumes": resumes_n,
        "by_group": by_group,
        "verdicts": verdicts,
        "workplace": workplace,
        "companies": db.list_companies(conn),
    }


@app.get("/api/postings/{posting_id}")
def posting_detail(posting_id: int):
    conn = _conn()
    r = db.get_posting(conn, posting_id)
    if not r:
        raise HTTPException(404, "no such posting")
    db.mark_seen(conn, posting_id)         # opening a posting flags it seen
    return _row(db.get_posting(conn, posting_id))


class SetStarred(BaseModel):
    starred: bool


@app.post("/api/postings/{posting_id}/star")
def star(posting_id: int, body: SetStarred):
    conn = _conn()
    if not db.get_posting(conn, posting_id):
        raise HTTPException(404, "no such posting")
    db.set_starred(conn, posting_id, body.starred)
    return {"ok": True, "starred": body.starred}


@app.post("/api/postings/{posting_id}/seen")
def seen(posting_id: int):
    conn = _conn()
    if not db.get_posting(conn, posting_id):
        raise HTTPException(404, "no such posting")
    db.mark_seen(conn, posting_id)
    return {"ok": True}


@app.post("/api/postings/{posting_id}/description")
def fetch_description(posting_id: int):
    """Backfill (or refresh) this one posting's JD — one network request."""
    from ..fetch import fetch_one_description

    cfg = load()
    conn = db.connect(cfg.db_path)
    if not db.get_posting(conn, posting_id):
        raise HTTPException(404, "no such posting")
    try:
        fetch_one_description(conn, cfg, posting_id)
    except Exception as e:
        raise HTTPException(502, f"fetch failed: {e}") from e
    return _row(db.get_posting(conn, posting_id))


# -- companies ------------------------------------------------------------


@app.get("/api/companies")
def companies():
    conn = _conn()
    return db.list_companies(conn)


class SetRating(BaseModel):
    name: str
    rating: int
    note: str | None = None


@app.post("/api/companies/rating")
def set_company_rating(body: SetRating):
    if not 0 <= body.rating <= 5:
        raise HTTPException(400, "rating must be 0-5")
    conn = _conn()
    db.set_company_rating(conn, body.name, body.rating, body.note)
    return {"ok": True}


class SetStatus(BaseModel):
    state: str
    note: str | None = None


@app.post("/api/postings/{posting_id}/status")
def set_status(posting_id: int, body: SetStatus):
    valid = {"new", "shortlisted", "applied", "passed", "ignored"}
    if body.state not in valid:
        raise HTTPException(400, f"state must be one of {sorted(valid)}")
    conn = _conn()
    if not db.get_posting(conn, posting_id):
        raise HTTPException(404, "no such posting")
    db.set_status(conn, posting_id, body.state, body.note)
    return {"ok": True}


# -- resumes --------------------------------------------------------------


@app.post("/api/postings/{posting_id}/resume")
def make_resume(posting_id: int):
    from ..assess import AssessError
    from ..resume import generate_resume

    cfg = load()
    conn = db.connect(cfg.db_path)
    row = db.get_posting(conn, posting_id)
    if not row:
        raise HTTPException(404, "no such posting")
    if not (row["description"] or "").strip():
        from ..fetch import fetch_one_description

        try:
            fetch_one_description(conn, cfg, posting_id)
        except Exception as e:
            raise HTTPException(502, f"could not fetch description: {e}") from e
    try:
        return generate_resume(conn, cfg, posting_id)
    except AssessError as e:
        raise HTTPException(502, f"resume generation failed: {e}") from e


@app.get("/api/postings/{posting_id}/resume")
def get_posting_resume(posting_id: int):
    conn = _conn()
    r = db.get_resume_for_posting(conn, posting_id)
    if not r:
        raise HTTPException(404, "no resume for this posting")
    return _row(r)


@app.get("/api/resumes")
def resumes():
    conn = _conn()
    return [_row(r) for r in db.list_resumes(conn)]


@app.get("/api/resumes/{resume_id}")
def resume_detail(resume_id: int):
    conn = _conn()
    r = db.get_resume(conn, resume_id)
    if not r:
        raise HTTPException(404, "no such resume")
    return _row(r)


@app.delete("/api/resumes/{resume_id}")
def resume_delete(resume_id: int):
    conn = _conn()
    db.delete_resume(conn, resume_id)
    return {"ok": True}


@app.post("/api/postings/{posting_id}/assess")
def assess_one(posting_id: int):
    from ..assess import AssessError, assess_posting

    cfg = load()
    conn = db.connect(cfg.db_path)
    row = db.get_posting(conn, posting_id)
    if not row:
        raise HTTPException(404, "no such posting")
    if not (row["description"] or "").strip():
        # fetch the JD inline, then assess
        from ..fetch import fetch_one_description

        try:
            fetch_one_description(conn, cfg, posting_id)
        except Exception as e:
            raise HTTPException(502, f"could not fetch description: {e}") from e
    try:
        assess_posting(conn, cfg, posting_id)
    except AssessError as e:
        raise HTTPException(502, f"assessment failed: {e}") from e
    return _row(db.get_posting(conn, posting_id))


# -- SPA (mounted last so /api/* wins) ------------------------------------

if (FRONTEND_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
