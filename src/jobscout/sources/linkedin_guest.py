"""LinkedIn unauthenticated 'guest' job search.

Two undocumented HTML endpoints:
  - seeMoreJobPostings/search  -> a <ul> of job cards (title/company/location/url/id/date)
  - jobPosting/<id>            -> the full description for one job

No login. Rate-limited (429s); blocked from datacenter IPs -> run locally.
Against LinkedIn User Agreement 8.2 — fine for a personal script, not for a product.
"""

from __future__ import annotations

import random
import re
import time

import httpx
from bs4 import BeautifulSoup

from ..config import Config, Location
from ..models import Posting
from .base import Source

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
PAGE_STEP = 25          # nominal page size; actual responses vary (often ~10 on start=0)
MAX_PAGES = 40          # LinkedIn hard-caps the guest endpoint near start=1000

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}

_JOB_ID_RE = re.compile(r"(\d{6,})")


class FetchError(RuntimeError):
    """Raised on repeated 429s / blocking so the caller can record a partial run."""


def _workplace_type(location_text: str, card) -> str | None:
    """Best-effort remote / hybrid / onsite from the guest card.

    The guest card rarely tags this explicitly, so we mostly read the location
    string ("Toronto, ON (Remote)"). Unknown -> None.
    """
    hay = location_text.lower()
    blob = card.get_text(" ", strip=True).lower() if card else ""
    for needle in (hay, blob):
        if "hybrid" in needle:
            return "hybrid"
        if "remote" in needle:
            return "remote"
    if "on-site" in blob or "on site" in blob:
        return "onsite"
    return None


class LinkedInGuestSource(Source):
    name = "linkedin_guest"

    def __init__(self, *, max_429_retries: int = 4) -> None:
        self._client = httpx.Client(headers=_HEADERS, timeout=20.0, follow_redirects=True)
        self._max_429_retries = max_429_retries

    # -- public API ----------------------------------------------------------

    def fetch(self, query: str, location: Location, window: str, cfg: Config) -> list[Posting]:
        out: list[Posting] = []
        seen_ids: set[str] = set()
        start = 0
        empty_streak = 0
        for _ in range(MAX_PAGES):
            if len(out) >= cfg.max_results_per_query:
                break
            html = self._get(
                SEARCH_URL,
                params=self._search_params(query, location, window, cfg, start),
                delay=cfg.request_delay_sec,
            )
            cards = self._parse_cards(html)
            if not cards:
                empty_streak += 1
                if empty_streak >= 2:      # two empty pages in a row = end of results
                    break
                start += PAGE_STEP
                continue
            empty_streak = 0
            added = 0
            for p in cards:
                if p.external_id in seen_ids:
                    continue
                seen_ids.add(p.external_id)
                p.matched_query = query
                p.matched_location = location.label
                out.append(p)
                added += 1
                if len(out) >= cfg.max_results_per_query:
                    break
            # advance by however many the endpoint actually returned
            start += len(cards)
            if added == 0:                 # a full page of dupes = nothing new left
                break
        return out

    def fetch_description(self, posting: Posting, cfg: Config) -> str:
        html = self._get(
            JOB_URL.format(job_id=posting.external_id),
            params=None,
            delay=cfg.request_delay_sec,
        )
        return self._parse_description(html)

    def close(self) -> None:
        self._client.close()

    # -- HTTP --------------------------------------------------------------

    def _get(self, url: str, params: dict | None, delay: tuple[float, float]) -> str:
        attempt = 0
        while True:
            time.sleep(random.uniform(*delay))
            resp = self._client.get(url, params=params)
            if resp.status_code == 429 or resp.status_code == 403:
                attempt += 1
                if attempt > self._max_429_retries:
                    raise FetchError(
                        f"LinkedIn returned {resp.status_code} {self._max_429_retries}x for {url} "
                        f"— rate-limited or IP-blocked. Try again later / from a residential IP."
                    )
                backoff = 30 * (2 ** (attempt - 1)) + random.uniform(0, 5)
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            return resp.text

    @staticmethod
    def _search_params(
        query: str, location: Location, window: str, cfg: Config, start: int
    ) -> dict:
        params: dict[str, str] = {
            "keywords": query,
            "location": location.label,
            "f_TPR": window,
            "sortBy": cfg.sort_by,
            "start": str(start),
        }
        if location.geo_id:
            params["geoId"] = location.geo_id
        if location.work_type:
            params["f_WT"] = str(location.work_type)
        if cfg.seniority:
            params["f_E"] = ",".join(str(s) for s in cfg.seniority)
        return params

    # -- parsing --------------------------------------------------------------

    @staticmethod
    def _parse_cards(html: str) -> list[Posting]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("li") or soup.select("div.base-card")
        out: list[Posting] = []
        for li in cards:
            card = li.select_one("div.base-card") or li
            job_id = LinkedInGuestSource._card_job_id(card)
            if not job_id:
                continue
            title_el = card.select_one(".base-search-card__title")
            company_el = card.select_one(".base-search-card__subtitle")
            loc_el = card.select_one(".job-search-card__location")
            link_el = card.select_one("a.base-card__full-link") or card.select_one("a[href]")
            time_el = card.select_one("time")

            url = ""
            if link_el and link_el.get("href"):
                url = link_el["href"].split("?")[0]
            elif job_id:
                url = f"https://www.linkedin.com/jobs/view/{job_id}"

            loc_txt = loc_el.get_text(strip=True) if loc_el else ""
            workplace_type = _workplace_type(loc_txt, card)

            out.append(
                Posting(
                    source="linkedin_guest",
                    external_id=job_id,
                    url=url,
                    title=title_el.get_text(strip=True) if title_el else "",
                    company=company_el.get_text(strip=True) if company_el else "",
                    location=loc_txt,
                    remote=(workplace_type == "remote") or None,
                    workplace_type=workplace_type,
                    posted_at=(time_el.get("datetime") if time_el else None),
                )
            )
        return out

    @staticmethod
    def _card_job_id(card) -> str | None:
        urn = card.get("data-entity-urn") or ""
        m = _JOB_ID_RE.search(urn)
        if m:
            return m.group(1)
        # fall back to the id embedded in the view link slug
        link = card.select_one("a[href*='/jobs/view/']")
        if link and link.get("href"):
            m = _JOB_ID_RE.search(link["href"])
            if m:
                return m.group(1)
        div = card.select_one("div[data-entity-urn]")
        if div:
            m = _JOB_ID_RE.search(div.get("data-entity-urn", ""))
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _parse_description(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        body = (
            soup.select_one(".show-more-less-html__markup")
            or soup.select_one(".description__text")
            or soup.select_one("div.description")
        )
        if not body:
            return ""
        for br in body.select("br"):
            br.replace_with("\n")
        text = body.get_text("\n", strip=True)
        return re.sub(r"\n{3,}", "\n\n", text).strip()
