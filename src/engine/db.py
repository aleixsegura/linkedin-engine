"""SQLite storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,              -- running | ok | error | cancelled
    n_cards INTEGER DEFAULT 0,
    n_new INTEGER DEFAULT 0,
    n_hidden_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,               -- source id (LinkedIn job id)
    canonical_id TEXT NOT NULL,        -- id of the original posting if this is a repost
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    company TEXT,
    company_norm TEXT,
    location TEXT,
    country TEXT,
    region TEXT,                       -- region label from config (e.g. ES | EU | US | UK)
    url TEXT,
    posted_at TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    first_run_id INTEGER REFERENCES runs(id),
    seniority TEXT,
    employment_type TEXT,
    workplace_type TEXT,               -- remote | hybrid | NULL
    description TEXT,
    desc_lang TEXT,
    relevance REAL,
    hidden_reason TEXT,                -- NULL | title | lang | clearance | low_score | no_detail
    no_sponsorship INTEGER DEFAULT 0,
    status TEXT                        -- reserved: applied | discarded | interesting
);
CREATE INDEX IF NOT EXISTS idx_jobs_canonical ON jobs(canonical_id);
CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(company_norm, title_norm, country);
CREATE INDEX IF NOT EXISTS idx_jobs_first_run ON jobs(first_run_id);

CREATE TABLE IF NOT EXISTS job_queries (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    query TEXT NOT NULL,
    region TEXT NOT NULL,
    PRIMARY KEY (job_id, query, region)
);

CREATE TABLE IF NOT EXISTS job_skills (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    skill TEXT NOT NULL,
    category TEXT NOT NULL,
    PRIMARY KEY (job_id, skill)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
