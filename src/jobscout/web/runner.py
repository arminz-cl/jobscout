"""In-process background runner. One job at a time, guarded by a lock.

Progress is written to the `runs` table by fetch.py, so the API just reads that
table back. This module owns the 'is something running' state and a cooperative
cancel flag (threads can't be force-killed, so run_fetch checks it between steps).
"""

from __future__ import annotations

import re
import threading
import time
import traceback
from dataclasses import dataclass, field

from .. import assess, db
from ..config import Config, load, resolve_window
from ..fetch import run_fetch


@dataclass
class JobState:
    run_id: int
    kind: str                 # fetch | assess
    groups: list[str] | None
    thread: threading.Thread
    cancel: threading.Event
    error: str | None = field(default=None)


_lock = threading.Lock()
_current: JobState | None = None
_activity = "idle"            # fine-grained live status for the UI
_activity_at = 0.0
_wait_until = 0.0             # unix ts the current backoff sleep ends (0 = not waiting)

_WAIT_RE = re.compile(r"waiting (\d+)s")


def _set_activity(msg: str) -> None:
    global _activity, _activity_at, _wait_until
    _activity = msg
    _activity_at = time.time()
    m = _WAIT_RE.search(msg)
    _wait_until = time.time() + int(m.group(1)) if m else 0.0


def activity() -> tuple[str, float, int]:
    remaining = max(0, round(_wait_until - time.time())) if _wait_until else 0
    return _activity, _activity_at, remaining


def current() -> JobState | None:
    with _lock:
        return _current


def is_busy() -> bool:
    with _lock:
        return _current is not None and _current.thread.is_alive()


def stop(run_id: int | None = None) -> bool:
    """Ask the running job to stop at the next checkpoint. Returns True if a job was signalled."""
    with _lock:
        if _current is None or not _current.thread.is_alive():
            return False
        if run_id is not None and _current.run_id != run_id:
            return False
        _current.cancel.set()
        return True


_PHASES = {"cards", "descriptions", "full", "triage", "deep"}
_KIND = {"cards": "fetch", "full": "fetch", "descriptions": "backfill", "triage": "triage", "deep": "deep"}


def start_fetch(
    groups: list[str] | None,
    since: str | None,
    *,
    phase: str = "full",
    then_assess: bool = False,
    assess_limit: int | None = None,
) -> int:
    """phase: cards / descriptions / full (fetch) · triage (batch-score level-0) ·
    deep (single-score top level-1). `then_assess` runs triage after a fetch.
    `assess_limit` caps postings for triage; count for deep."""
    global _current
    if phase not in _PHASES:
        raise RuntimeError(f"phase must be one of {sorted(_PHASES)}")
    with _lock:
        if _current is not None and _current.thread.is_alive():
            raise RuntimeError(f"a {_current.kind} run (#{_current.run_id}) is already in progress")

        cfg = load()
        effective_groups = groups if groups is not None else list(cfg.mode_groups)
        conn = db.connect(cfg.db_path)
        last_run = db.last_successful_run_iso(conn, cfg.source)
        window = resolve_window(cfg, since, last_run)
        kind = _KIND[phase]
        run_id = db.start_run(
            conn, cfg.source, cfg.mode, window, groups=effective_groups, kind=kind
        )
        conn.close()

        cancel = threading.Event()
        thread = threading.Thread(
            target=_run_job,
            args=(cfg, run_id, effective_groups, since, phase, then_assess, assess_limit, cancel),
            name=f"jobscout-{phase}-{run_id}",
            daemon=True,
        )
        _current = JobState(run_id, kind, effective_groups, thread, cancel)
        thread.start()
        return run_id


def _run_job(
    cfg: Config,
    run_id: int,
    groups: list[str] | None,
    since: str | None,
    phase: str,
    then_assess: bool,
    assess_limit: int | None,
    cancel: threading.Event,
) -> None:
    conn = db.connect(cfg.db_path)
    assess.STATUS = _set_activity
    _set_activity(f"starting {phase}")
    try:
        if phase == "triage":
            # for a triage phase, `groups` (if the caller passed a subset) narrows to those areas
            tg = groups if groups is not None and groups != list(cfg.mode_groups) else None
            _triage(conn, cfg, run_id, cancel, scope_run_id=None, groups=tg, limit=assess_limit)
        elif phase == "deep":
            bg = groups if groups is not None and groups != list(cfg.mode_groups) else None
            _deep(conn, cfg, run_id, cancel, limit=assess_limit or 15, balance_groups=bg)
        else:
            result = run_fetch(
                conn, cfg, groups=groups, since=since, verbose=True, run_id=run_id, cancel=cancel,
                do_cards=phase in ("cards", "full"),
                skip_descriptions=phase == "cards",
            )
            if then_assess and not (cancel and cancel.is_set()):
                _triage(conn, cfg, run_id, cancel, scope_run_id=result.run_id, limit=assess_limit)
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        traceback.print_exc()
        db.finish_run(conn, run_id, status="failed", counts={}, note=f"{type(e).__name__}: {e}")
        _set_activity(f"failed: {e}")
        with _lock:
            if _current and _current.run_id == run_id:
                _current.error = str(e)
    finally:
        assess.STATUS = None
        _set_activity("idle")
        conn.close()


def _triage(conn, cfg, run_id, cancel, *, scope_run_id, groups=None, limit=None) -> None:
    from ..assess import triage_pending

    def progress(done, failed, total):
        db.update_run_progress(
            conn, run_id, {"assessed": done}, note=f"triage {done}/{total} ({failed} failed)"
        )

    res = triage_pending(
        conn, cfg, run_id=scope_run_id, groups=groups, limit=limit, progress=progress, cancel=cancel
    )
    _finish_assess(conn, run_id, res, cancel)


def _deep(conn, cfg, run_id, cancel, *, limit, balance_groups=None) -> None:
    from ..assess import deep_top

    def progress(done, failed, total):
        db.update_run_progress(
            conn, run_id, {"assessed": done}, note=f"deep {done}/{total} ({failed} failed)"
        )

    res = deep_top(
        conn, cfg, limit=limit, balance_groups=balance_groups, progress=progress, cancel=cancel
    )
    _finish_assess(conn, run_id, res, cancel)


def _finish_assess(conn, run_id, res, cancel) -> None:
    status = "stopped" if (cancel and cancel.is_set()) else ("partial" if res["failed"] else "ok")
    db.finish_run(
        conn, run_id, status=status,
        counts={"assessed": res["assessed"]},
        note=f"{res['assessed']} assessed, {res['failed']} failed",
    )
