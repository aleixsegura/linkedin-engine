"""Orchestration of a full engine run."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine import analysis
from engine.config import Config
from engine.sources.base import JobCard, Source

log = logging.getLogger(__name__)

REPOST_WINDOW_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def last_successful_run(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT started_at FROM runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return datetime.fromisoformat(row["started_at"]) if row else None


def should_run(conn: sqlite3.Connection, cfg: Config, now: datetime) -> bool:
    last = last_successful_run(conn)
    return last is None or now - last >= timedelta(days=cfg.min_days_between_runs)


def backup_db(conn: sqlite3.Connection, backups_dir: Path, keep: int, now: datetime) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    target = backups_dir / f"jobs-{now:%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(target) as dst:
        conn.backup(dst)
    for old in sorted(backups_dir.glob("jobs-*.db"))[:-keep]:
        old.unlink()
    return target


def find_original(
    conn: sqlite3.Connection, job_id: str, company_norm: str, title_norm: str, country: str | None, now: datetime
) -> str | None:
    """If the job is a repost of one seen in the last 30 days, return its canonical_id."""
    since = (now - timedelta(days=REPOST_WINDOW_DAYS)).isoformat()
    row = conn.execute(
        """SELECT canonical_id FROM jobs
           WHERE company_norm = ? AND title_norm = ? AND country IS ? AND id != ? AND last_seen >= ?
           ORDER BY first_seen LIMIT 1""",
        (company_norm, title_norm, country, job_id, since),
    ).fetchone()
    return row["canonical_id"] if row else None


def collect_cards(
    source: Source, cfg: Config, max_queries: int | None
) -> dict[str, tuple[JobCard, set[tuple[str, str]]]]:
    """Run every search and group cards by id."""
    combos = [(q, r, g) for q in cfg.queries for r, g in cfg.regions.items()]
    if max_queries is not None:
        combos = combos[:max_queries]
    cards: dict[str, tuple[JobCard, set[tuple[str, str]]]] = {}
    for i, (query, region, geo_id) in enumerate(combos, 1):
        n = 0
        for card in source.search(query, region, geo_id):
            n += 1
            entry = cards.setdefault(card.id, (card, set()))
            entry[1].add((query, region))
            if card.country and not entry[0].country:
                entry[0].country = card.country
        log.info("[%d/%d] %s @ %s: %d results", i, len(combos), query, region, n)
    return cards


def _insert_new_job(
    conn: sqlite3.Connection,
    source: Source,
    cfg: Config,
    card: JobCard,
    region: str,
    run_id: int,
    now: datetime,
) -> str | None:
    title_norm = analysis.normalize_title(card.title)
    company_norm = analysis.normalize_company(card.company)
    canonical = find_original(conn, card.id, company_norm, title_norm, card.country, now) or card.id

    detail = None
    skills: dict[str, str] = {}
    lang = workplace = None
    score = None
    no_sponsorship = False
    if analysis.title_excluded(card.title, cfg):
        hidden = "title"
    else:
        detail = source.detail(card.id)
        a = analysis.assess(card.title, card.location, detail.description if detail else "", cfg)
        skills, lang, workplace, score = a.skills, a.lang, a.workplace, a.relevance
        no_sponsorship = a.no_sponsorship
        hidden = a.hidden_reason if detail else (a.hidden_reason or "no_detail")

    conn.execute(
        """INSERT INTO jobs (id, canonical_id, source, title, title_norm, company, company_norm, location,
               country, region, url, posted_at, first_seen, last_seen, first_run_id, seniority,
               employment_type, workplace_type, description, desc_lang, relevance, hidden_reason,
               no_sponsorship)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card.id, canonical, card.source, card.title, title_norm, card.company, company_norm,
            card.location, card.country, region, card.url, card.posted_at, now.isoformat(),
            now.isoformat(), run_id, detail.seniority if detail else None,
            detail.employment_type if detail else None, workplace,
            detail.description if detail else None, lang, score, hidden, int(no_sponsorship),
        ),
    )
    conn.executemany(
        "INSERT INTO job_skills (job_id, skill, category) VALUES (?, ?, ?)",
        [(card.id, s, c) for s, c in skills.items()],
    )
    return hidden if canonical == card.id else "repost"


def rescore(conn: sqlite3.Connection, cfg: Config) -> Counter[str]:
    """Re-apply filters, skills and relevance to every stored job using the current config."""
    outcomes: Counter[str] = Counter()
    rows = conn.execute("SELECT id, title, location, description FROM jobs").fetchall()
    for r in rows:
        conn.execute("DELETE FROM job_skills WHERE job_id = ?", (r["id"],))
        if analysis.title_excluded(r["title"], cfg):
            conn.execute("UPDATE jobs SET hidden_reason = 'title' WHERE id = ?", (r["id"],))
            outcomes["title"] += 1
            continue
        a = analysis.assess(r["title"], r["location"] or "", r["description"] or "", cfg)
        hidden = a.hidden_reason if r["description"] else (a.hidden_reason or "no_detail")
        conn.execute(
            """UPDATE jobs SET hidden_reason = ?, relevance = ?, desc_lang = ?, workplace_type = ?,
                   no_sponsorship = ? WHERE id = ?""",
            (hidden, a.relevance, a.lang, a.workplace, int(a.no_sponsorship), r["id"]),
        )
        conn.executemany(
            "INSERT INTO job_skills (job_id, skill, category) VALUES (?, ?, ?)",
            [(r["id"], s, c) for s, c in a.skills.items()],
        )
        outcomes[hidden or "visible"] += 1
    conn.commit()
    return outcomes


def run(
    conn: sqlite3.Connection,
    source: Source,
    cfg: Config,
    *,
    max_queries: int | None = None,
) -> int:
    """Run the searches, store new jobs and return the run id."""
    now = _now()
    run_id = conn.execute(
        "INSERT INTO runs (started_at, status) VALUES (?, 'running')", (now.isoformat(),)
    ).lastrowid
    conn.commit()
    try:
        cards = collect_cards(source, cfg, max_queries)
        outcomes: Counter[str] = Counter()
        new_ids = []
        for job_id, (card, hits) in cards.items():
            exists = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if exists:
                conn.execute("UPDATE jobs SET last_seen = ? WHERE id = ?", (now.isoformat(), job_id))
            else:
                search_region = sorted(hits, key=lambda h: h[1] == "EU")[0][1]
                region = analysis.region_for(card.country, search_region)
                outcome = _insert_new_job(conn, source, cfg, card, region, run_id, now)
                outcomes[outcome or "visible"] += 1
                new_ids.append(job_id)
                if len(new_ids) % 25 == 0:
                    log.info("Processed %d new jobs", len(new_ids))
            conn.executemany(
                "INSERT OR IGNORE INTO job_queries (job_id, query, region) VALUES (?, ?, ?)",
                [(job_id, q, r) for q, r in hits],
            )
            conn.commit()
        conn.execute(
            """UPDATE runs SET finished_at = ?, status = 'ok', n_cards = ?, n_new = ?, n_hidden_json = ?
               WHERE id = ?""",
            (_now().isoformat(), len(cards), outcomes["visible"], json.dumps(dict(outcomes)), run_id),
        )
        conn.commit()
        log.info("Run %d: %d cards, %d new -> %s", run_id, len(cards), len(new_ids), dict(outcomes))
        return run_id
    except BaseException as exc:
        conn.rollback()
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = 'error', error = ? WHERE id = ?",
            (_now().isoformat(), repr(exc), run_id),
        )
        conn.commit()
        raise
