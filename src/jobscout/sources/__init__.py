"""Pluggable job sources. Each adapter implements the Source interface in base.py."""

from __future__ import annotations

from .base import Source


def get_source(name: str) -> Source:
    if name == "linkedin_guest":
        from .linkedin_guest import LinkedInGuestSource

        return LinkedInGuestSource()
    if name == "jsearch":
        raise NotImplementedError("jsearch adapter lands in Phase 6")
    raise ValueError(f"unknown source: {name!r}")
