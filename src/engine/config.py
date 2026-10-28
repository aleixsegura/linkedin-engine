"""Carga de configuración (config.yaml y skills.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
BACKUPS_DIR = ROOT / "backups"
DB_PATH = DATA_DIR / "jobs.db"


def _compile(patterns: list[str] | None) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in (patterns or [])]


@dataclass
class WeightedPatterns:
    weight: float
    patterns: list[re.Pattern]


@dataclass
class Scoring:
    threshold: float
    title_terms: WeightedPatterns
    engineering_title_terms: WeightedPatterns
    negative_terms: WeightedPatterns
    geo_skill_weight: float
    geo_skill_cap: int


@dataclass
class Config:
    queries: list[str]
    regions: dict[str, int]
    posted_within_seconds: int
    max_results_per_query: int
    min_delay: float
    max_delay: float
    max_retries: int
    min_days_between_runs: float
    backups_keep: int
    title_exclude: list[re.Pattern]
    allowed_languages: set[str]
    foreign_language_requirements: list[re.Pattern]
    clearance_requirements: list[re.Pattern]
    no_sponsorship: list[re.Pattern]
    workplace: dict[str, list[re.Pattern]]
    scoring: Scoring
    skills: dict[str, dict[str, list[re.Pattern]]] = field(default_factory=dict)


def _weighted(d: dict) -> WeightedPatterns:
    return WeightedPatterns(weight=d["weight"], patterns=_compile(d.get("patterns")))


def _skill_patterns(name: str, patterns: list[str] | None) -> list[re.Pattern]:
    if patterns:
        return _compile(patterns)
    return [re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)]


def load_skills(path: Path = CONFIG_DIR / "skills.yaml") -> dict[str, dict[str, list[re.Pattern]]]:
    raw = yaml.safe_load(path.read_text())
    return {
        category: {name: _skill_patterns(name, pats) for name, pats in skills.items()}
        for category, skills in raw.items()
    }


def load_config(path: Path = CONFIG_DIR / "config.yaml") -> Config:
    raw = yaml.safe_load(path.read_text())
    search, sc = raw["search"], raw["scoring"]
    return Config(
        queries=raw["queries"],
        regions={k: int(v) for k, v in raw["regions"].items()},
        posted_within_seconds=search["posted_within_seconds"],
        max_results_per_query=search["max_results_per_query"],
        min_delay=search["min_delay_seconds"],
        max_delay=search["max_delay_seconds"],
        max_retries=search["max_retries"],
        min_days_between_runs=raw["schedule"]["min_days_between_runs"],
        backups_keep=raw["backups"]["keep"],
        title_exclude=_compile(raw["title_exclude"]),
        allowed_languages=set(raw["allowed_description_languages"]),
        foreign_language_requirements=_compile(raw["foreign_language_requirements"]),
        clearance_requirements=_compile(raw["clearance_requirements"]),
        no_sponsorship=_compile(raw["no_sponsorship"]),
        workplace={k: _compile(v) for k, v in raw["workplace"].items()},
        scoring=Scoring(
            threshold=sc["threshold"],
            title_terms=_weighted(sc["title_terms"]),
            engineering_title_terms=_weighted(sc["engineering_title_terms"]),
            negative_terms=_weighted(sc["negative_terms"]),
            geo_skill_weight=sc["geo_skill_weight"],
            geo_skill_cap=sc["geo_skill_cap"],
        ),
        skills=load_skills(),
    )
