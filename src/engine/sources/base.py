"""Common interface for job sources."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass
class JobCard:
    """Search result (no description)."""

    id: str
    source: str
    title: str
    company: str
    location: str
    country: str | None  # lowercase ISO-3166 alpha-2, if known
    url: str
    posted_at: str | None  # YYYY-MM-DD


@dataclass
class JobDetail:
    description: str
    seniority: str | None
    employment_type: str | None


class Source(Protocol):
    name: str

    def search(self, query: str, region: str, geo_id: int) -> Iterator[JobCard]: ...

    def detail(self, job_id: str) -> JobDetail | None: ...
