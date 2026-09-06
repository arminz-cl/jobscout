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
    do_cards: bool = True,
    verbose: bool = True,
    run_id: int | None = None,
    cancel: threading.Event | None = None,
) -> FetchResult:
    """Run a fetch. `do_cards=False` skips the searches and only backfills
    descriptions for postings already in the DB that lack one."""
    source = get_source(cfg.source)
    last_run = db.last_successful_run_iso(conn, cfg.source)
    window = resolve_window(cfg, since, last_run)
    pairs = cfg.group_query_pairs(groups) if do_cards else []
    effective_groups = groups if groups is not None else list(cfg.mode_groups)
    if run_id is None:
        kind = "fetch" if do_cards else "backfill"
        run_id = db.start_run(
            conn, cfg.source, cfg.mode, window, groups=effective_groups, kind=kind
        )

    stopped = cancel.is_set() if cancel else False

    if verbose:
        label = ",".join(groups) if groups else cfg.mode
        print(f"source={cfg.source}  groups={label}  window={window}  "
              f"queries={len(pairs)}  locations={len(cfg.locations)}  cards={do_cards}")

    fetched = 0
    seen_ext: set[str] = set()
    new_ids: list[int] = []
    partial = False
    note = None

    try:
        for qi, (group_name, query) in enumerate(pairs, 1):
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
                    card.matched_group = group_name
                    pid, is_new = db.upsert_posting(conn, card, run_id=run_id)
                    if is_new:
                        new_ids.append(pid)
                        new_here += 1
                conn.commit()
                db.update_run_progress(
                    conn, run_id,
                    {"fetched": fetched, "unique": len(seen_ext), "new": len(new_ids)},
                    note=f"searching {qi}/{len(pairs)} @ {loc.label}",
                )
                if verbose:
                    print(f"  [{qi}/{len(pairs)}] {group_name:16s} {loc.label:24s} "
                          f"{len(cards):3d} cards  (+{new_here} new)")
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


def fetch_one_description(conn: sqlite3.Connection, cfg: Config, posting_id: int) -> str:
    """Fetch (or re-fetch) the description for a single posting. Used by the
    per-posting backfill button. One network request."""
    row = db.get_posting(conn, posting_id)
    if not row:
        raise ValueError(f"no posting {posting_id}")
    source = get_source(cfg.source)
    p = Posting(source=row["source"], external_id=row["external_id"], url=row["url"],
                title=row["title"], company=row["company"], location=row["location"])
    text = source.fetch_description(p, cfg)
    db.set_description(conn, posting_id, text, _sniff_comp(text))
    if hasattr(source, "close"):
        source.close()
    return text
