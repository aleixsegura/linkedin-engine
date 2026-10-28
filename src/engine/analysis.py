"""Job analysis: filters, language, skills, relevance and normalization for dedup."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from langdetect import DetectorFactory, LangDetectException, detect

from engine.config import Config

DetectorFactory.seed = 0

COUNTRY_REGION = {"es": "ES", "uk": "UK", "gb": "UK", "us": "US"}

# Common title noise: gender markers, work mode, codes in brackets.
_TITLE_NOISE = re.compile(
    r"\((m|f|w|d|h|x|/)+\)|\b(m|f|w|d|h)/(m|f|w|d|h)(/(m|f|w|d|h|x))?\b|\([^)]*\)|\[[^\]]*\]"
    r"|\b(remote|hybrid|remoto|h[ií]brido)\b",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^\w]+")
_COMPANY_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|limited|gmbh|s\.?a\.?|s\.?l\.?|plc|corp|corporation|co|ag|bv|b\.v)\b\.?",
    re.IGNORECASE,
)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_title(title: str) -> str:
    t = _TITLE_NOISE.sub(" ", _strip_accents(title).lower())
    return _NON_WORD.sub(" ", t).strip()


def normalize_company(company: str) -> str:
    c = _COMPANY_SUFFIX.sub(" ", _strip_accents(company or "").lower())
    return _NON_WORD.sub(" ", c).strip()


def region_for(country: str | None, search_region: str) -> str:
    return COUNTRY_REGION.get(country or "", search_region)


def title_excluded(title: str, cfg: Config) -> bool:
    return any(p.search(title) for p in cfg.title_exclude)


def detect_language(text: str) -> str | None:
    try:
        return detect(text[:3000])
    except LangDetectException:
        return None


def extract_skills(text: str, cfg: Config) -> dict[str, str]:
    """Return {skill: category} for skills found in the text."""
    found = {}
    for category, skills in cfg.skills.items():
        for name, patterns in skills.items():
            if any(p.search(text) for p in patterns):
                found[name] = category
    return found


def detect_workplace(text: str, location: str, cfg: Config) -> str | None:
    haystack = f"{location}\n{text}"
    for kind in ("remote", "hybrid"):
        if any(p.search(haystack) for p in cfg.workplace.get(kind, [])):
            # "hybrid" wins if present, since many hybrid jobs also mention "remote".
            if kind == "remote" and any(p.search(haystack) for p in cfg.workplace.get("hybrid", [])):
                return "hybrid"
            return kind
    return None


def relevance(title: str, skills: dict[str, str], cfg: Config) -> float:
    sc = cfg.scoring
    score = 0.0
    if any(p.search(title) for p in sc.title_terms.patterns):
        score += sc.title_terms.weight
    if any(p.search(title) for p in sc.engineering_title_terms.patterns):
        score += sc.engineering_title_terms.weight
    if any(p.search(title) for p in sc.negative_terms.patterns):
        score += sc.negative_terms.weight
    geo = sum(1 for cat in skills.values() if cat.startswith("geo_"))
    score += min(geo, sc.geo_skill_cap) * sc.geo_skill_weight
    return score


@dataclass
class Assessment:
    lang: str | None
    skills: dict[str, str]
    workplace: str | None
    relevance: float
    no_sponsorship: bool
    hidden_reason: str | None


def assess(title: str, location: str, description: str, cfg: Config) -> Assessment:
    lang = detect_language(description)
    skills = extract_skills(f"{title}\n{description}", cfg)
    score = relevance(title, skills, cfg)
    if lang is not None and lang not in cfg.allowed_languages:
        hidden = "lang"
    elif any(p.search(description) for p in cfg.foreign_language_requirements):
        hidden = "lang"
    elif any(p.search(description) for p in cfg.clearance_requirements):
        hidden = "clearance"
    elif score < cfg.scoring.threshold:
        hidden = "low_score"
    else:
        hidden = None
    return Assessment(
        lang=lang,
        skills=skills,
        workplace=detect_workplace(description, location, cfg),
        relevance=score,
        no_sponsorship=any(p.search(description) for p in cfg.no_sponsorship),
        hidden_reason=hidden,
    )
