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
    area             TEXT,
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
        "workplace_type, description, comp_raw, posted_at, matched_query, first_seen_at, "
        "last_seen_at, first_run_id, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            p.source, p.external_id, p.url, p.title, p.company, p.location,
            None if p.remote is None else int(p.remote), p.workplace_type,
            p.description, p.comp_raw, p.posted_at, p.matched_query,
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


# -- read API (used by the web backend) ----------------------------------

_POSTING_SELECT = """
SELECT p.*,
       a.area, a.target_quality, a.chance, a.verdict, a.overall,
       a.gaps_hit, a.comp_vs_baseline, a.assessed_at,
       s.state AS status_state, s.note AS status_note
FROM postings p
LEFT JOIN assessments a ON a.posting_id = p.id
LEFT JOIN status s ON s.posting_id = p.id
"""


def list_postings(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    queries: list[str] | None = None,
    run_id: int | None = None,
    company: str | None = None,
    verdict: str | None = None,
    assessed: bool | None = None,
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
    if queries:
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
        "verdict": "CASE a.verdict WHEN 'pursue' THEN 0 WHEN 'maybe' THEN 1 WHEN 'skip' THEN 2 ELSE 3 END, p.first_seen_at DESC",
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
    return [
        {"name": r["company"], "count": r["n"]}
        for r in conn.execute(
            "SELECT company, COUNT(*) AS n FROM postings WHERE company != '' "
            "GROUP BY company ORDER BY n DESC, company COLLATE NOCASE"
        )
    ]


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
    row = conn.execute(
        "SELECT finished_at FROM runs WHERE source = ? AND status IN ('ok', 'partial') "
        "AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1",
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
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, n_fetched = ?, n_unique = ?, "
        "n_new = ?, n_assessed = ?, note = ? WHERE id = ?",
        (
            now_iso(), status,
            counts.get("fetched", 0), counts.get("unique", 0),
            counts.get("new", 0), counts.get("assessed", 0),
            note, run_id,
        ),
    )
    conn.commit()
