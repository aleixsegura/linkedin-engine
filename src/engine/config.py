"""Configuration loading (config.yaml and skills.yaml)."""

from __future__ import annotations

import copy
import fnmatch
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

# Per-user overrides (git-ignored), merged on top of the tracked defaults.
LOCAL_CONFIG = CONFIG_DIR / "config.local.yaml"
LOCAL_SKILLS = CONFIG_DIR / "skills.local.yaml"
LOCAL_CONFIG_EXAMPLE = CONFIG_DIR / "config.local.example.yaml"
# Top-level mappings that a local config replaces instead of merging.
REPLACED_KEYS = {"regions"}


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
    domain_skill_categories: list[str]
    domain_skill_weight: float
    domain_skill_cap: int

    def is_domain(self, category: str) -> bool:
        """Whether a skill category counts towards relevance (glob match against domain_skill_categories)."""
        return any(fnmatch.fnmatchcase(category, pat) for pat in self.domain_skill_categories)


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


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into a copy of `base`. Non-dict values (including lists) are replaced."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_yaml(path: Path, local: Path | None, replaced: set[str] = frozenset()) -> dict:
    raw = yaml.safe_load(path.read_text()) or {}
    if local is not None and local.exists():
        override = yaml.safe_load(local.read_text()) or {}
        raw = deep_merge({k: v for k, v in raw.items() if k not in override or k not in replaced}, override)
    return raw


_NO_TERMS = {"weight": 0, "patterns": []}


def _weighted(d: dict) -> WeightedPatterns:
    return WeightedPatterns(weight=d["weight"], patterns=_compile(d.get("patterns")))


def _skill_patterns(name: str, patterns: list[str] | None) -> list[re.Pattern]:
    if patterns:
        return _compile(patterns)
    return [re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)]


def load_skills(
    path: Path = CONFIG_DIR / "skills.yaml", local: Path | None = LOCAL_SKILLS
) -> dict[str, dict[str, list[re.Pattern]]]:
    raw = _read_yaml(path, local)
    return {
        category: {name: _skill_patterns(name, pats) for name, pats in skills.items()}
        for category, skills in raw.items()
    }


def load_config(
    path: Path = CONFIG_DIR / "config.yaml",
    local: Path | None = LOCAL_CONFIG,
    skills_local: Path | None = LOCAL_SKILLS,
) -> Config:
    """Load the tracked defaults, merged with the user's local overrides if present (pass None to skip)."""
    raw = _read_yaml(path, local, REPLACED_KEYS)
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
            engineering_title_terms=_weighted(sc.get("engineering_title_terms", _NO_TERMS)),
            negative_terms=_weighted(sc.get("negative_terms", _NO_TERMS)),
            domain_skill_categories=list(sc.get("domain_skill_categories", [])),
            domain_skill_weight=sc.get("domain_skill_weight", 1),
            domain_skill_cap=sc.get("domain_skill_cap", 5),
        ),
        skills=load_skills(local=skills_local),
    )
