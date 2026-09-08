"""SQLite persistence. One file (jobscout.db), created on first use."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Posting, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    url           TEXT,
    title         TEXT,
    company       TEXT,
    location      TEXT,
    remote        INTEGER,
    workplace_type TEXT,                  -- remote | hybrid | onsite | NULL
    description   TEXT,
    comp_raw      TEXT,
    posted_at     TEXT,
    matched_query TEXT,
    matched_group TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    first_run_id  INTEGER,                -- the run that first inserted this posting
    seen_at       TEXT,                   -- set the first time it's opened in the UI
    starred       INTEGER NOT NULL DEFAULT 0,
    raw_json      TEXT,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS assessments (
    id               INTEGER PRIMARY KEY,
    posting_id       INTEGER NOT NULL REFERENCES postings(id),
    method           TEXT,                -- batch | single
    score_overall    INTEGER,             -- 0-100
    score_chance     INTEGER,
    score_quality    INTEGER,
    area             TEXT,
    area_reason      TEXT,
    target_quality   TEXT,
    chance           TEXT,
    verdict          TEXT,
    overall          TEXT,
    keep_de_titles   INTEGER,
    gaps_hit         TEXT,
    comp_vs_baseline TEXT,
    model            TEXT,
    assessed_at      TEXT NOT NULL,
    UNIQUE (posting_id)
);

CREATE TABLE IF NOT EXISTS status (
    posting_id INTEGER PRIMARY KEY REFERENCES postings(id),
    state      TEXT NOT NULL,        -- new | shortlisted | applied | passed | ignored
    note       TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    name       TEXT PRIMARY KEY,
    rating     INTEGER NOT NULL DEFAULT 0,   -- 0-5, manual for now
    note       TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS resumes (
    id         INTEGER PRIMARY KEY,
    posting_id INTEGER NOT NULL REFERENCES postings(id),
    base       TEXT,                          -- which base resume (A / B) was tailored
    content    TEXT NOT NULL,                 -- the tailored resume, markdown
    notes      TEXT,                          -- tailoring notes + verify-before-sending flags
    model      TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (posting_id)
);

-- interactive resume-builder chat: one session per posting, plus its messages
CREATE TABLE IF NOT EXISTS resume_sessions (
    id            INTEGER PRIMARY KEY,
    posting_id    INTEGER NOT NULL REFERENCES postings(id),
    base          TEXT,                        -- base resume label (A / B)
    model         TEXT,
    profile_brief TEXT,                        -- condensed profile, built once at start
    ledger        TEXT,                        -- running "established facts" the agent maintains
    draft         TEXT,                        -- current tailored resume markdown
    notes         TEXT,                        -- verify-before-sending / tailoring notes
    status        TEXT NOT NULL DEFAULT 'open',-- open | saved
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (posting_id)
);

CREATE TABLE IF NOT EXISTS resume_messages (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES resume_sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,                  -- user | assistant
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY,
    source           TEXT NOT NULL,
    mode             TEXT,
    groups           TEXT,            -- comma-separated query groups this run covered
    kind             TEXT,            -- fetch | assess | run
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    status           TEXT,            -- running | ok | partial | failed
    time_posted_used TEXT,
    n_fetched        INTEGER DEFAULT 0,
    n_unique         INTEGER DEFAULT 0,
    n_new            INTEGER DEFAULT 0,
    n_assessed       INTEGER DEFAULT 0,
    note             TEXT
);
"""

# columns added after the first schema shipped — applied to existing DBs
_MIGRATIONS = [
    "ALTER TABLE runs ADD COLUMN groups TEXT",
    "ALTER TABLE runs ADD COLUMN kind TEXT",
    "ALTER TABLE postings ADD COLUMN seen_at TEXT",
    "ALTER TABLE postings ADD COLUMN starred INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE postings ADD COLUMN first_run_id INTEGER",
    "ALTER TABLE postings ADD COLUMN workplace_type TEXT",
    "ALTER TABLE postings ADD COLUMN matched_group TEXT",
    "ALTER TABLE assessments ADD COLUMN area_reason TEXT",
    "ALTER TABLE assessments ADD COLUMN method TEXT",
    "ALTER TABLE assessments ADD COLUMN score_overall INTEGER",
    "ALTER TABLE assessments ADD COLUMN score_chance INTEGER",
    "ALTER TABLE assessments ADD COLUMN score_quality INTEGER",
]


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn


# -- postings --------------------------------------------------------------


def upsert_posting(
    conn: sqlite3.Connection, p: Posting, *, run_id: int | None = None
) -> tuple[int, bool]:
    """Insert or refresh a posting. Returns (posting_id, is_new)."""
    now = now_iso()
    row = conn.execute(
        "SELECT id FROM postings WHERE source = ? AND external_id = ?",
        (p.source, p.external_id),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE postings SET last_seen_at = ?, url = COALESCE(NULLIF(?, ''), url), "
            "title = COALESCE(NULLIF(?, ''), title), company = COALESCE(NULLIF(?, ''), company), "
            "location = COALESCE(NULLIF(?, ''), location) WHERE id = ?",
            (now, p.url, p.title, p.company, p.location, row["id"]),
        )
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO postings (source, external_id, url, title, company, location, remote, "
        "workplace_type, description, comp_raw, posted_at, matched_query, matched_group, "
        "first_seen_at, last_seen_at, first_run_id, raw_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            p.source, p.external_id, p.url, p.title, p.company, p.location,
            None if p.remote is None else int(p.remote), p.workplace_type,
            p.description, p.comp_raw, p.posted_at, p.matched_query, p.matched_group,
            now, now, run_id, json.dumps(p.raw or {}),
        ),
    )
    return cur.lastrowid, True


def set_description(conn: sqlite3.Connection, posting_id: int, description: str, comp_raw: str | None) -> None:
    conn.execute(
        "UPDATE postings SET description = ?, comp_raw = COALESCE(?, comp_raw) WHERE id = ?",
        (description, comp_raw, posting_id),
    )


def postings_missing_description(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM postings WHERE description IS NULL OR description = '' ORDER BY id"
    ).fetchall()


# -- resumes -------------------------------------------------------------


def store_resume(
    conn: sqlite3.Connection, posting_id: int, *, base: str, content: str, notes: str, model: str
) -> int:
    cur = conn.execute(
        "INSERT INTO resumes (posting_id, base, content, notes, model, created_at) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(posting_id) DO UPDATE SET base=excluded.base, content=excluded.content, "
        "notes=excluded.notes, model=excluded.model, created_at=excluded.created_at",
        (posting_id, base, content, notes, model, now_iso()),
    )
    conn.commit()
    return cur.lastrowid or conn.execute(
        "SELECT id FROM resumes WHERE posting_id = ?", (posting_id,)
    ).fetchone()[0]


def get_resume_for_posting(conn: sqlite3.Connection, posting_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM resumes WHERE posting_id = ?", (posting_id,)).fetchone()


def get_resume(conn: sqlite3.Connection, resume_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT r.*, p.title, p.company, p.url FROM resumes r JOIN postings p ON p.id = r.posting_id "
        "WHERE r.id = ?",
        (resume_id,),
    ).fetchone()


def list_resumes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT r.id, r.posting_id, r.base, r.model, r.created_at, "
        "p.title, p.company, a.verdict, a.score_overall "
        "FROM resumes r JOIN postings p ON p.id = r.posting_id "
        "LEFT JOIN assessments a ON a.posting_id = p.id "
        "ORDER BY r.created_at DESC"
    ).fetchall()


def delete_resume(conn: sqlite3.Connection, resume_id: int) -> None:
    conn.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
    conn.commit()


# -- resume-builder sessions -------------------------------------------


def get_resume_session(conn: sqlite3.Connection, posting_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM resume_sessions WHERE posting_id = ?", (posting_id,)
    ).fetchone()


def get_resume_session_by_id(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT s.*, p.title, p.company, p.url FROM resume_sessions s "
        "JOIN postings p ON p.id = s.posting_id WHERE s.id = ?",
        (session_id,),
    ).fetchone()


def create_resume_session(
    conn: sqlite3.Connection, posting_id: int, *, base: str, model: str, profile_brief: str
) -> int:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO resume_sessions "
        "(posting_id, base, model, profile_brief, ledger, draft, notes, status, created_at, updated_at) "
        "VALUES (?,?,?,?,'','','','open',?,?)",
        (posting_id, base, model, profile_brief, ts, ts),
    )
    conn.commit()
    return cur.lastrowid


def update_resume_session(conn: sqlite3.Connection, session_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE resume_sessions SET {cols} WHERE id = ?",
        (*fields.values(), session_id),
    )
    conn.commit()


def delete_resume_session(conn: sqlite3.Connection, posting_id: int) -> None:
    row = conn.execute(
        "SELECT id FROM resume_sessions WHERE posting_id = ?", (posting_id,)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM resume_messages WHERE session_id = ?", (row["id"],))
        conn.execute("DELETE FROM resume_sessions WHERE id = ?", (row["id"],))
        conn.commit()


def add_resume_message(conn: sqlite3.Connection, session_id: int, role: str, content: str) -> None:
    conn.execute(
        "INSERT INTO resume_messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
        (session_id, role, content, now_iso()),
    )
    conn.commit()


def list_resume_messages(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT role, content, created_at FROM resume_messages "
        "WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()


# -- assessments --------------------------------------------------------------


def postings_pending_assessment(
    conn: sqlite3.Connection,
    *,
    run_id: int | None = None,
    groups: list[str] | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """Level 0: have a description, no assessment yet — for the triage (batch) pass."""
    sql = (
        "SELECT p.* FROM postings p LEFT JOIN assessments a ON a.posting_id = p.id "
        "WHERE a.id IS NULL AND p.description IS NOT NULL AND p.description != ''"
    )
    params: list = []
    if run_id is not None:
        sql += " AND p.first_run_id = ?"
        params.append(run_id)
    if groups:
        sql += f" AND p.matched_group IN ({','.join('?' * len(groups))})"
        params += groups
    sql += " ORDER BY p.first_seen_at DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def postings_for_deep(
    conn: sqlite3.Connection, *, limit: int = 15, balance_groups: list[str] | None = None
) -> list[sqlite3.Row]:
    """Level 1: triaged (method='batch') but not deep-assessed — highest score first.

    `balance_groups`: interleave the highest-scored postings across these groups
    (A#1, B#1, A#2, B#2, …) instead of a single global ranking.
    """
    base = (
        "SELECT p.* FROM postings p JOIN assessments a ON a.posting_id = p.id "
        "WHERE a.method = 'batch' AND p.description IS NOT NULL AND p.description != '' "
    )
    if not balance_groups:
        return conn.execute(
            base + "ORDER BY a.score_overall DESC, p.first_seen_at DESC LIMIT ?", (limit,)
        ).fetchall()

    per = {
        g: conn.execute(
            base + "AND p.matched_group = ? ORDER BY a.score_overall DESC, p.first_seen_at DESC LIMIT ?",
            (g, limit),
        ).fetchall()
        for g in balance_groups
    }
    out: list = []
    i = 0
    while len(out) < limit and any(i < len(per[g]) for g in balance_groups):
        for g in balance_groups:
            if i < len(per[g]) and len(out) < limit:
                out.append(per[g][i])
        i += 1
    return out


def assessment_counts(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    with_desc = conn.execute(
        "SELECT COUNT(*) FROM postings WHERE description IS NOT NULL AND description != ''"
    ).fetchone()[0]
    batch = conn.execute("SELECT COUNT(*) FROM assessments WHERE method = 'batch'").fetchone()[0]
    single = conn.execute("SELECT COUNT(*) FROM assessments WHERE method = 'single'").fetchone()[0]
    return {
        "level0": with_desc - batch - single,   # has description, not assessed
        "level1": batch,                          # triaged
        "level2": single,                         # deep
        "no_description": total - with_desc,
    }


_ASSESS_COLS = [
    "posting_id", "method", "model", "score_overall", "score_chance", "score_quality",
    "area", "area_reason", "target_quality", "chance", "verdict", "overall",
    "keep_de_titles", "gaps_hit", "comp_vs_baseline", "assessed_at",
]


def store_assessment(conn: sqlite3.Connection, a) -> None:
    """Upsert one Assessment (see models.Assessment)."""
    vals = (
        a.posting_id, a.method, a.model, a.score_overall, a.score_chance, a.score_quality,
        a.area, a.area_reason, a.target_quality, a.chance, a.verdict, a.overall,
        int(a.keep_de_titles), json.dumps(a.gaps_hit), a.comp_vs_baseline, a.assessed_at,
    )
    placeholders = ",".join("?" * len(_ASSESS_COLS))
    updates = ",".join(f"{c}=excluded.{c}" for c in _ASSESS_COLS if c != "posting_id")
    conn.execute(
        f"INSERT INTO assessments ({','.join(_ASSESS_COLS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(posting_id) DO UPDATE SET {updates}",
        vals,
    )
    conn.commit()


# -- read API (used by the web backend) ----------------------------------

_POSTING_SELECT = """
SELECT p.*,
       a.method AS assess_method,
       a.score_overall, a.score_chance, a.score_quality,
       a.area, a.area_reason, a.target_quality, a.chance, a.verdict, a.overall,
       a.gaps_hit, a.comp_vs_baseline, a.keep_de_titles, a.model AS assessed_model,
       a.assessed_at,
       s.state AS status_state, s.note AS status_note,
       r.id AS resume_id
FROM postings p
LEFT JOIN assessments a ON a.posting_id = p.id
LEFT JOIN status s ON s.posting_id = p.id
LEFT JOIN resumes r ON r.posting_id = p.id
"""


def list_postings(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    group: str | None = None,
    queries: list[str] | None = None,
    run_id: int | None = None,
    company: str | None = None,
    verdict: str | None = None,
    assessed: bool | None = None,
    level: int | None = None,             # 0 none · 1 triaged · 2 deep
    has_resume: bool | None = None,
    has_description: bool | None = None,
    seen: bool | None = None,
    starred: bool | None = None,
    status: str | None = None,
    search: str | None = None,
    order: str = "first_seen_at",
    limit: int = 100,
    offset: int = 0,
) -> list[sqlite3.Row]:
    where, params = [], []
    if query:
        where.append("p.matched_query = ?")
        params.append(query)
    if group and queries:
        # match the denormalized group column (new rows) OR the query set (old rows)
        where.append(
            f"(p.matched_group = ? OR p.matched_query IN ({','.join('?' * len(queries))}))"
        )
        params += [group, *queries]
    elif group:
        where.append("p.matched_group = ?")
        params.append(group)
    elif queries:
        where.append(f"p.matched_query IN ({','.join('?' * len(queries))})")
        params += queries
    if run_id is not None:
        where.append("p.first_run_id = ?")
        params.append(run_id)
    if company:
        where.append("p.company = ?")
        params.append(company)
    if verdict:
        where.append("a.verdict = ?")
        params.append(verdict)
    if assessed is True:
        where.append("a.id IS NOT NULL")
    elif assessed is False:
        where.append("a.id IS NULL")
    if level == 0:
        where.append("a.id IS NULL")
    elif level == 1:
        where.append("a.method = 'batch'")
    elif level == 2:
        where.append("a.method = 'single'")
    if has_resume is True:
        where.append("r.id IS NOT NULL")
    elif has_resume is False:
        where.append("r.id IS NULL")
    if has_description is True:
        where.append("p.description IS NOT NULL AND p.description != ''")
    elif has_description is False:
        where.append("(p.description IS NULL OR p.description = '')")
    if seen is True:
        where.append("p.seen_at IS NOT NULL")
    elif seen is False:
        where.append("p.seen_at IS NULL")
    if starred is True:
        where.append("p.starred = 1")
    elif starred is False:
        where.append("p.starred = 0")
    if status:
        where.append("s.state = ?" if status != "none" else "s.state IS NULL")
        if status != "none":
            params.append(status)
    if search:
        where.append("(p.title LIKE ? OR p.company LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    order_col = {
        "first_seen_at": "p.first_seen_at DESC",
        "posted_at": "p.posted_at DESC",
        "company": "p.company COLLATE NOCASE",
        "verdict": "CASE a.verdict WHEN 'pursue' THEN 0 WHEN 'maybe' THEN 1 WHEN 'skip' THEN 2 ELSE 3 END, a.score_overall DESC",
        "score": "a.score_overall DESC NULLS LAST, p.first_seen_at DESC",
        "score_chance": "a.score_chance DESC NULLS LAST",
        "score_quality": "a.score_quality DESC NULLS LAST",
    }.get(order, "p.first_seen_at DESC")
    sql = _POSTING_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order_col} LIMIT ? OFFSET ?"
    params += [limit, offset]
    return conn.execute(sql, params).fetchall()


def get_posting(conn: sqlite3.Connection, posting_id: int) -> sqlite3.Row | None:
    return conn.execute(_POSTING_SELECT + " WHERE p.id = ?", (posting_id,)).fetchone()


def mark_seen(conn: sqlite3.Connection, posting_id: int) -> None:
    conn.execute(
        "UPDATE postings SET seen_at = ? WHERE id = ? AND seen_at IS NULL",
        (now_iso(), posting_id),
    )
    conn.commit()


def set_starred(conn: sqlite3.Connection, posting_id: int, starred: bool) -> None:
    conn.execute("UPDATE postings SET starred = ? WHERE id = ?", (int(starred), posting_id))
    conn.commit()


def dashboard_counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN description IS NOT NULL AND description != '' THEN 1 ELSE 0 END) AS with_desc "
        "FROM postings"
    ).fetchone()
    verdicts = {
        r["verdict"]: r["n"]
        for r in conn.execute("SELECT verdict, COUNT(*) AS n FROM assessments GROUP BY verdict")
    }
    assessed = conn.execute("SELECT COUNT(*) AS n FROM assessments").fetchone()["n"]
    by_query = [
        dict(r)
        for r in conn.execute(
            "SELECT matched_query AS query, COUNT(*) AS n FROM postings "
            "GROUP BY matched_query ORDER BY n DESC"
        )
    ]
    return {
        "postings": row["total"] or 0,
        "with_description": row["with_desc"] or 0,
        "assessed": assessed,
        "verdicts": verdicts,
        "by_query": by_query,
    }


def list_runs(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def list_companies(conn: sqlite3.Connection) -> list[dict]:
    """Every company seen in postings, with its (manual) rating and posting stats."""
    rows = conn.execute(
        """
        SELECT p.company AS name,
               COUNT(*) AS count,
               SUM(CASE WHEN a.verdict = 'pursue' THEN 1 ELSE 0 END) AS pursue,
               SUM(CASE WHEN p.starred = 1 THEN 1 ELSE 0 END) AS starred,
               COALESCE(c.rating, 0) AS rating,
               c.note AS note
        FROM postings p
        LEFT JOIN assessments a ON a.posting_id = p.id
        LEFT JOIN companies c ON c.name = p.company
        WHERE p.company != ''
        GROUP BY p.company
        ORDER BY COALESCE(c.rating, 0) DESC, count DESC, name COLLATE NOCASE
        """
    ).fetchall()
    return [dict(r) for r in rows]


def set_company_rating(
    conn: sqlite3.Connection, name: str, rating: int, note: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO companies (name, rating, note, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET rating = excluded.rating, "
        "note = COALESCE(excluded.note, companies.note), updated_at = excluded.updated_at",
        (name, rating, note, now_iso()),
    )
    conn.commit()


def set_status(conn: sqlite3.Connection, posting_id: int, state: str, note: str | None) -> None:
    conn.execute(
        "INSERT INTO status (posting_id, state, note, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(posting_id) DO UPDATE SET state = excluded.state, "
        "note = excluded.note, updated_at = excluded.updated_at",
        (posting_id, state, note, now_iso()),
    )
    conn.commit()


# -- runs -----------------------------------------------------------------


def last_successful_run_iso(conn: sqlite3.Connection, source: str) -> str | None:
    """Most recent run that actually ran the searches (kind 'fetch' or legacy NULL).

    Backfill-only runs are excluded — they don't advance the search window.
    """
    row = conn.execute(
        "SELECT finished_at FROM runs WHERE source = ? AND status IN ('ok', 'partial') "
        "AND (kind = 'fetch' OR kind IS NULL) AND finished_at IS NOT NULL "
        "ORDER BY finished_at DESC LIMIT 1",
        (source,),
    ).fetchone()
    return row["finished_at"] if row else None


def start_run(
    conn: sqlite3.Connection,
    source: str,
    mode: str,
    window: str,
    *,
    groups: list[str] | None = None,
    kind: str = "fetch",
) -> int:
    cur = conn.execute(
        "INSERT INTO runs (source, mode, groups, kind, started_at, status, time_posted_used) "
        "VALUES (?,?,?,?,?,?,?)",
        (source, mode, ",".join(groups) if groups else None, kind, now_iso(), "running", window),
    )
    conn.commit()
    return cur.lastrowid


def update_run_progress(conn: sqlite3.Connection, run_id: int, counts: dict, note: str | None = None) -> None:
    """Live progress ping — safe to call often; keeps the runs row current for the UI."""
    conn.execute(
        "UPDATE runs SET n_fetched = ?, n_unique = ?, n_new = ?, n_assessed = ?, "
        "note = COALESCE(?, note) WHERE id = ?",
        (
            counts.get("fetched", 0), counts.get("unique", 0),
            counts.get("new", 0), counts.get("assessed", 0),
            note, run_id,
        ),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: int, *, status: str, counts: dict, note: str | None = None) -> None:
    """Mark a run finished. Only the count keys present in `counts` are overwritten;
    the rest keep whatever `update_run_progress` last wrote."""
    sets = ["finished_at = ?", "status = ?", "note = ?"]
    params: list = [now_iso(), status, note]
    for key, col in [
        ("fetched", "n_fetched"), ("unique", "n_unique"),
        ("new", "n_new"), ("assessed", "n_assessed"),
    ]:
        if key in counts:
            sets.append(f"{col} = ?")
            params.append(counts[key])
    params.append(run_id)
    conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
