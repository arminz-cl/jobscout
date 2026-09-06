"""Core data types passed between fetch / store / assess / report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def now_iso() -> str:
    """Current time as a tz-aware ISO string in the machine's local zone.

    Storing with the offset (e.g. ...-06:00) keeps it unambiguous *and* readable
    in local time when browsing the DB directly.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(slots=True)
class Posting:
    """One job posting, as returned by a Source. `external_id` is unique per source."""

    source: str
    external_id: str
    url: str
    title: str
    company: str
    location: str
    remote: bool | None = None
    workplace_type: str | None = None     # remote | hybrid | onsite | None (unknown)
    description: str | None = None
    comp_raw: str | None = None
    posted_at: str | None = None          # ISO date if known
    raw: dict = field(default_factory=dict)

    # populated by the query that surfaced it (useful for debugging match quality)
    matched_query: str | None = None
    matched_group: str | None = None
    matched_location: str | None = None


@dataclass(slots=True)
class Assessment:
    """LLM verdict for a posting. Mirrors the assessment-guide rubric."""

    posting_id: int
    area: str                             # A | B | C | D
    target_quality: str                   # high | med | low
    chance: str                           # high | med | low
    verdict: str                          # pursue | maybe | skip
    overall: str                          # one-line reasoning
    keep_de_titles: bool
    gaps_hit: list[str]
    comp_vs_baseline: str                 # above | within | below | unknown
    model: str
    assessed_at: str = field(default_factory=now_iso)
