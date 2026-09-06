"""Orchestrate a fetch: queries x locations -> Source -> store -> descriptions."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

from . import db
from .config import Config, resolve_window
from .models import Posting
from .sources import get_source
from .sources.linkedin_guest import FetchError


@dataclass
class FetchResult:
    run_id: int
    window: str
    fetched: int          # raw cards across all query/location searches
    unique: int           # distinct postings after in-run dedupe
    new: int              # not previously in the DB
    descriptions: int     # descriptions fetched this run
    partial: bool         # True if a FetchError cut the run short
    stopped: bool = False  # True if a cancel was requested mid-run
    note: str | None = None


def run_fetch(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    groups: list[str] | None = None,
    since: str | None = None,
    skip_descriptions: bool = False,
    verbose: bool = True,
    run_id: int | None = None,
    cancel: threading.Event | None = None,
) -> FetchResult:
    source = get_source(cfg.source)
    last_run = db.last_successful_run_iso(conn, cfg.source)
    window = resolve_window(cfg, since, last_run)
    queries = cfg.queries_for(groups)
    if run_id is None:
        run_id = db.start_run(conn, cfg.source, cfg.mode, window)

    stopped = cancel.is_set() if cancel else False

    if verbose:
        label = ",".join(groups) if groups else cfg.mode
        print(f"source={cfg.source}  groups={label}  window={window}  "
              f"queries={len(queries)}  locations={len(cfg.locations)}")

    fetched = 0
    seen_ext: set[str] = set()
    new_ids: list[int] = []
    partial = False
    note = None

    try:
        for qi, query in enumerate(queries, 1):
            if cancel and cancel.is_set():
                stopped = True
                break
            for loc in cfg.locations:
                cards = source.fetch(query, loc, window, cfg)
                fetched += len(cards)
                new_here = 0
                for card in cards:
                    if card.external_id in seen_ext:
                        continue
                    seen_ext.add(card.external_id)
                    pid, is_new = db.upsert_posting(conn, card, run_id=run_id)
                    if is_new:
                        new_ids.append(pid)
                        new_here += 1
                conn.commit()
                db.update_run_progress(
                    conn, run_id,
                    {"fetched": fetched, "unique": len(seen_ext), "new": len(new_ids)},
                    note=f"searching {qi}/{len(queries)} @ {loc.label}",
                )
                if verbose:
                    print(f"  [{qi}/{len(queries)}] {loc.label:28s} "
                          f"{len(cards):3d} cards  (+{new_here} new)  :: {query[:60]}")
    except FetchError as e:
        partial = True
        note = str(e)
        if verbose:
            print(f"  ! {e}")

    descriptions = 0
    if not skip_descriptions and not stopped:
        missing = db.postings_missing_description(conn)
        if verbose and missing:
            print(f"fetching {len(missing)} descriptions...")
        total_missing = len(missing)
        try:
            for row in missing:
                if cancel and cancel.is_set():
                    stopped = True
                    break
                p = Posting(source=row["source"], external_id=row["external_id"], url=row["url"],
                            title=row["title"], company=row["company"], location=row["location"])
                text = source.fetch_description(p, cfg)
                comp = _sniff_comp(text)
                db.set_description(conn, row["id"], text, comp)
                conn.commit()
                descriptions += 1
                if descriptions % 5 == 0 or descriptions == total_missing:
                    db.update_run_progress(
                        conn, run_id,
                        {"fetched": fetched, "unique": len(seen_ext), "new": len(new_ids)},
                        note=f"descriptions {descriptions}/{total_missing}",
                    )
                    if verbose:
                        print(f"  descriptions {descriptions}/{total_missing}")
        except FetchError as e:
            partial = True
            note = (note + " | " if note else "") + str(e)
            if verbose:
                print(f"  ! {e}")

    if hasattr(source, "close"):
        source.close()

    if stopped:
        note = (note + " | " if note else "") + "stopped by user"
    final_status = "stopped" if stopped else ("partial" if partial else "ok")
    result = FetchResult(
        run_id=run_id, window=window, fetched=fetched, unique=len(seen_ext),
        new=len(new_ids), descriptions=descriptions, partial=partial, stopped=stopped, note=note,
    )
    db.finish_run(
        conn, run_id,
        status=final_status,
        counts={"fetched": fetched, "unique": len(seen_ext), "new": len(new_ids)},
        note=note,
    )
    return result


def _sniff_comp(text: str) -> str | None:
    """Cheap comp extraction from a JD — a single $-range line if present."""
    import re

    m = re.search(r"(\$[\d,]{4,}(?:\s*[-–—to]+\s*\$?[\d,]{4,})?(?:\s*(?:per year|/yr|annually|CAD|USD))?)", text)
    return m.group(1).strip() if m else None
