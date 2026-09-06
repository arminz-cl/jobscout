"""In-process background runner. One job at a time, guarded by a lock.

Progress is written to the `runs` table by fetch.py, so the API just reads that
table back. This module owns the 'is something running' state and a cooperative
cancel flag (threads can't be force-killed, so run_fetch checks it between steps).
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field

from .. import db
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


def start_fetch(groups: list[str] | None, since: str | None, *, assess: bool = False) -> int:
    global _current
    with _lock:
        if _current is not None and _current.thread.is_alive():
            raise RuntimeError(f"a {_current.kind} run (#{_current.run_id}) is already in progress")

        cfg = load()
        conn = db.connect(cfg.db_path)
        last_run = db.last_successful_run_iso(conn, cfg.source)
        window = resolve_window(cfg, since, last_run)
        run_id = db.start_run(conn, cfg.source, cfg.mode, window, groups=groups, kind="fetch")
        conn.close()

        cancel = threading.Event()
        thread = threading.Thread(
            target=_run_fetch_job,
            args=(cfg, run_id, groups, since, assess, cancel),
            name=f"jobscout-fetch-{run_id}",
            daemon=True,
        )
        _current = JobState(run_id, "fetch", groups, thread, cancel)
        thread.start()
        return run_id


def _run_fetch_job(
    cfg: Config,
    run_id: int,
    groups: list[str] | None,
    since: str | None,
    assess: bool,
    cancel: threading.Event,
) -> None:
    conn = db.connect(cfg.db_path)
    try:
        run_fetch(conn, cfg, groups=groups, since=since, verbose=True, run_id=run_id, cancel=cancel)
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        traceback.print_exc()
        db.finish_run(conn, run_id, status="failed", counts={}, note=f"{type(e).__name__}: {e}")
        with _lock:
            if _current and _current.run_id == run_id:
                _current.error = str(e)
    finally:
        conn.close()
