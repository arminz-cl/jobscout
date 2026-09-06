"""Load and validate config.yaml, resolve the active query set and time window."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

# LinkedIn f_TPR only reliably honors these three windows.
WINDOW_24H = "r86400"
WINDOW_7D = "r604800"
WINDOW_30D = "r2592000"
VALID_WINDOWS = {WINDOW_24H, WINDOW_7D, WINDOW_30D}
SINCE_ALIASES = {"24h": WINDOW_24H, "7d": WINDOW_7D, "30d": WINDOW_30D}


@dataclass(slots=True, frozen=True)
class Location:
    label: str
    geo_id: str | None = None
    work_type: int | None = None          # 1 on-site, 2 remote, 3 hybrid


@dataclass(slots=True, frozen=True)
class QueryGroup:
    name: str                             # machine key, e.g. "area_a"
    title: str                            # display title, e.g. "A · SWE · data platforms"
    summary: str                          # one-line "what this group targets" for tooltips
    queries: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class Config:
    profile_dir: Path
    eval_dir: Path
    output_dir: Path
    model: str
    temperature: float
    source: str
    request_delay_sec: tuple[float, float]
    max_results_per_query: int
    mode: str
    locations: list[Location]
    time_posted: str                      # "auto" or a pinned window
    sort_by: str
    seniority: list[int]
    query_groups: dict[str, QueryGroup] = field(default_factory=dict)
    mode_groups: list[str] = field(default_factory=list)   # group names active in `mode`
    queries: list[str] = field(default_factory=list)       # flattened queries for `mode`

    @property
    def db_path(self) -> Path:
        return REPO_ROOT / "jobscout.db"

    @property
    def daily_dir(self) -> Path:
        return self.output_dir / "jds" / "daily"

    def queries_for(self, groups: list[str] | None) -> list[str]:
        """Flatten the given group names to a deduped query list.

        `None` -> the groups active in the current mode.
        """
        names = groups if groups is not None else self.mode_groups
        out: list[str] = []
        seen: set[str] = set()
        for name in names:
            if name not in self.query_groups:
                raise ConfigError(f"unknown query group: {name!r}")
            for q in self.query_groups[name].queries:
                if q not in seen:
                    seen.add(q)
                    out.append(q)
        return out

    def group_query_pairs(self, groups: list[str] | None) -> list[tuple[str, str]]:
        """(group_name, query) in run order, first group wins a shared query."""
        names = groups if groups is not None else self.mode_groups
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name in names:
            if name not in self.query_groups:
                raise ConfigError(f"unknown query group: {name!r}")
            for q in self.query_groups[name].queries:
                if q not in seen:
                    seen.add(q)
                    out.append((name, q))
        return out


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


class ConfigError(RuntimeError):
    pass


def _resolve_groups(raw: dict) -> tuple[dict[str, QueryGroup], list[str]]:
    """Return (all query_groups, group names active in the current mode).

    Each group is `{title, summary, queries}`; a bare list of queries is also
    accepted for backward compatibility.
    """
    mode = raw.get("mode", "casual")
    modes = raw.get("modes", {})
    groups: dict[str, QueryGroup] = {}
    for name, spec in (raw.get("query_groups", {}) or {}).items():
        if isinstance(spec, list):
            queries, title, summary = spec, name, ""
        else:
            queries = spec.get("queries", [])
            title = spec.get("title") or name
            summary = spec.get("summary", "")
        if not queries:
            raise ConfigError(f"query group {name!r} has no queries")
        groups[name] = QueryGroup(name=name, title=title, summary=summary, queries=tuple(queries))
    if mode not in modes:
        raise ConfigError(f"mode {mode!r} not found in `modes:` ({sorted(modes)})")
    mode_groups = list(modes[mode])
    for g in mode_groups:
        if g not in groups:
            raise ConfigError(f"query group {g!r} (used by mode {mode!r}) not in `query_groups:`")
    return groups, mode_groups


def load(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"no config at {path} — copy config.example.yaml to config.yaml and edit it")
    raw = yaml.safe_load(path.read_text()) or {}

    try:
        paths = raw["paths"]
        profile_dir = _expand(paths["profile_dir"])
        eval_dir = _expand(paths["eval_dir"])
        output_dir = _expand(paths["output_dir"])
    except KeyError as e:
        raise ConfigError(f"missing required config key: {e}") from e

    for name, p in [("profile_dir", profile_dir), ("output_dir", output_dir)]:
        if not p.exists():
            raise ConfigError(f"{name} does not exist: {p}")

    time_posted = str(raw.get("time_posted", "auto"))
    if time_posted != "auto" and time_posted not in VALID_WINDOWS:
        raise ConfigError(f"time_posted must be 'auto' or one of {sorted(VALID_WINDOWS)}, got {time_posted!r}")

    delay = raw.get("request_delay_sec", [2, 4])
    locations = [
        Location(label=loc["label"], geo_id=loc.get("geo_id"), work_type=loc.get("work_type"))
        for loc in raw.get("locations", [])
    ]
    if not locations:
        raise ConfigError("at least one entry in `locations:` is required")

    query_groups, mode_groups = _resolve_groups(raw)
    seen: set[str] = set()
    queries = [
        q
        for g in mode_groups
        for q in query_groups[g].queries
        if not (q in seen or seen.add(q))
    ]
    if not queries:
        raise ConfigError(f"mode {raw.get('mode', 'casual')!r} resolved to zero queries")

    cfg = Config(
        profile_dir=profile_dir,
        eval_dir=eval_dir,
        output_dir=output_dir,
        model=raw.get("model", "claude-sonnet-5"),
        temperature=float(raw.get("temperature", 0)),
        source=raw.get("source", "linkedin_guest"),
        request_delay_sec=(float(delay[0]), float(delay[1])),
        max_results_per_query=int(raw.get("max_results_per_query", 75)),
        mode=raw.get("mode", "casual"),
        locations=locations,
        time_posted=time_posted,
        sort_by=raw.get("sort_by", "DD"),
        seniority=list(raw.get("seniority", []) or []),
        query_groups=query_groups,
        mode_groups=mode_groups,
        queries=queries,
    )
    return cfg


def resolve_window(cfg: Config, since: str | None, last_run_iso: str | None) -> str:
    """Pick the f_TPR window for this run.

    Priority: explicit --since  >  pinned config value  >  auto (from last run gap).
    """
    if since:
        if since not in SINCE_ALIASES:
            raise ConfigError(f"--since must be one of {sorted(SINCE_ALIASES)}, got {since!r}")
        return SINCE_ALIASES[since]

    if cfg.time_posted != "auto":
        return cfg.time_posted

    if not last_run_iso:
        return WINDOW_30D                  # first run -> 30d backfill

    from datetime import datetime

    last = datetime.fromisoformat(last_run_iso)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    gap_h = (datetime.now(UTC) - last).total_seconds() / 3600
    if gap_h <= 24:
        return WINDOW_24H
    if gap_h <= 24 * 7:
        return WINDOW_7D
    return WINDOW_30D
