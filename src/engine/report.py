"""HTML report generation: new jobs and skill trends."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

_env = Environment(loader=PackageLoader("engine"), autoescape=select_autoescape())

HIDDEN_LABELS = {
    "title": "excluded title (analyst, technician, internship…)",
    "lang": "require another language",
    "clearance": "require citizenship / clearance",
    "low_score": "low relevance",
    "no_detail": "no description available",
    "repost": "reposts of jobs already seen",
}
# Jobs that don't count towards the skills history (no description or off-topic).
TREND_EXCLUDED = ("title", "low_score", "no_detail")


def _skills_by_job(conn: sqlite3.Connection, job_ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    if not job_ids:
        return out
    marks = ",".join("?" * len(job_ids))
    for r in conn.execute(
        f"SELECT job_id, skill, category FROM job_skills WHERE job_id IN ({marks}) ORDER BY category, skill",
        job_ids,
    ):
        out[r["job_id"]].append({"name": r["skill"], "category": r["category"]})
    return out


def render_new_jobs(conn: sqlite3.Connection, run_id: int, reports_dir: Path) -> Path:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    jobs = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM jobs WHERE first_run_id = ? AND canonical_id = id AND hidden_reason IS NULL
               ORDER BY relevance DESC, posted_at DESC""",
            (run_id,),
        )
    ]
    skills = _skills_by_job(conn, [j["id"] for j in jobs])
    for j in jobs:
        j["skills"] = skills.get(j["id"], [])
    hidden = {
        HIDDEN_LABELS.get(r["reason"], r["reason"]): r["n"]
        for r in conn.execute(
            """SELECT CASE WHEN canonical_id != id THEN 'repost' ELSE hidden_reason END AS reason,
                      COUNT(*) AS n
               FROM jobs WHERE first_run_id = ? AND (canonical_id != id OR hidden_reason IS NOT NULL)
               GROUP BY reason ORDER BY n DESC""",
            (run_id,),
        )
    }
    by_region: dict[str, int] = defaultdict(int)
    for j in jobs:
        by_region[j["region"]] += 1

    html = _env.get_template("new_jobs.html.j2").render(
        run=dict(run),
        jobs=jobs,
        hidden=hidden,
        by_region=dict(sorted(by_region.items())),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{run['started_at'][:10]}-run{run_id}.html"
    path.write_text(html)
    shutil.copyfile(path, reports_dir / "latest.html")
    return path


def trend_dataset(conn: sqlite3.Connection) -> dict:
    """Compact data for the trends page (aggregated in the browser)."""
    marks = ",".join("?" * len(TREND_EXCLUDED))
    rows = conn.execute(
        f"""SELECT id, first_seen, region FROM jobs
            WHERE canonical_id = id AND (hidden_reason IS NULL OR hidden_reason NOT IN ({marks}))""",
        TREND_EXCLUDED,
    ).fetchall()
    skill_rows = conn.execute("SELECT DISTINCT skill, category FROM job_skills ORDER BY category, skill").fetchall()
    skill_index = {r["skill"]: i for i, r in enumerate(skill_rows)}
    per_job = _skills_by_job(conn, [r["id"] for r in rows])
    jobs = []
    for r in rows:
        iso = datetime.fromisoformat(r["first_seen"]).isocalendar()
        jobs.append(
            [f"{iso.year}-W{iso.week:02d}", r["region"], [skill_index[s["name"]] for s in per_job.get(r["id"], [])]]
        )
    return {
        "skills": [[r["skill"], r["category"]] for r in skill_rows],
        "jobs": jobs,
    }


def render_trends(conn: sqlite3.Connection, reports_dir: Path) -> Path:
    data = trend_dataset(conn)
    html = _env.get_template("trends.html.j2").render(
        data_json=json.dumps(data, ensure_ascii=False),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_jobs=len(data["jobs"]),
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "trends.html"
    path.write_text(html)
    return path
