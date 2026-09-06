"""The Source interface. Adapters isolate all site-specific / ToS-grey logic."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import Config, Location
from ..models import Posting


class Source(ABC):
    name: str

    @abstractmethod
    def fetch(self, query: str, location: Location, window: str, cfg: Config) -> list[Posting]:
        """Return job-card-level Postings (no description) for one query+location."""

    @abstractmethod
    def fetch_description(self, posting: Posting, cfg: Config) -> str:
        """Return the full JD text for a posting. May be a second network call."""
