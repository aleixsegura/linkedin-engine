"""LinkedIn source via the public "guest" endpoints (no login)."""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Iterator

import httpx
from selectolax.lexbor import LexborHTMLParser

from engine.sources.base import JobCard, JobDetail

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
PAGE_SIZE = 10

_SUBDOMAIN_RE = re.compile(r"https?://([a-z]{2,3})\.linkedin\.com")
_URN_RE = re.compile(r"jobPosting:(\d+)")


def _text(node) -> str:
    return node.text(strip=True) if node is not None else ""


def parse_search(html: str, region: str) -> list[JobCard]:
    cards = []
    for card in LexborHTMLParser(html).css("div.base-search-card"):
        urn = _URN_RE.search(card.attributes.get("data-entity-urn") or "")
        link = card.css_first("a.base-card__full-link")
        if not urn or link is None:
            continue
        href = link.attributes.get("href") or ""
        sub = _SUBDOMAIN_RE.match(href)
        country = sub.group(1) if sub and sub.group(1) != "www" else None
        if country is None and region == "US":
            country = "us"
        time_node = card.css_first("time")
        cards.append(
            JobCard(
                id=urn.group(1),
                source="linkedin",
                title=_text(card.css_first("h3.base-search-card__title")),
                company=_text(card.css_first("h4.base-search-card__subtitle")),
                location=_text(card.css_first(".job-search-card__location")),
                country=country,
                url=href.split("?")[0],
                posted_at=time_node.attributes.get("datetime") if time_node else None,
            )
        )
    return cards


def parse_detail(html: str) -> JobDetail | None:
    tree = LexborHTMLParser(html)
    desc = tree.css_first(".show-more-less-html__markup")
    if desc is None:
        return None
    criteria = {}
    for item in tree.css("li.description__job-criteria-item"):
        criteria[_text(item.css_first("h3")).lower()] = _text(item.css_first("span"))
    return JobDetail(
        description=desc.text(separator="\n", strip=True),
        seniority=criteria.get("seniority level"),
        employment_type=criteria.get("employment type"),
    )


class LinkedInGuestSource:
    name = "linkedin"

    def __init__(
        self,
        posted_within_seconds: int,
        max_results: int,
        min_delay: float,
        max_delay: float,
        max_retries: int,
    ) -> None:
        self.posted_within_seconds = posted_within_seconds
        self.max_results = max_results
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        # Requests that still failed after every retry (rate limiting or network errors).
        self.failed_requests = 0
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            timeout=30,
            follow_redirects=True,
        )

    def _get(self, url: str, params: dict | None = None) -> str | None:
        for attempt in range(self.max_retries + 1):
            time.sleep(random.uniform(self.min_delay, self.max_delay))
            try:
                resp = self.client.get(url, params=params)
            except httpx.HTTPError as exc:
                log.warning("Network error %s: %s", url, exc)
            else:
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code in (400, 404):
                    return None
                log.warning("HTTP %s on %s (attempt %d)", resp.status_code, url, attempt + 1)
            time.sleep(min(10 * 2**attempt, 120))
        self.failed_requests += 1
        return None

    def search(self, query: str, region: str, geo_id: int) -> Iterator[JobCard]:
        seen: set[str] = set()
        start = 0
        while start < self.max_results:
            html = self._get(
                SEARCH_URL,
                {
                    "keywords": query,
                    "geoId": geo_id,
                    "f_TPR": f"r{self.posted_within_seconds}",
                    "start": start,
                },
            )
            if not html:
                return
            cards = [c for c in parse_search(html, region) if c.id not in seen]
            if not cards:
                return
            for card in cards:
                seen.add(card.id)
                yield card
            start += PAGE_SIZE

    def detail(self, job_id: str) -> JobDetail | None:
        html = self._get(DETAIL_URL.format(id=job_id))
        return parse_detail(html) if html else None
