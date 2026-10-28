"""CLI: `engine run | report | trends | backup`."""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from typing import Annotated

import typer

from engine import db, pipeline, report
from engine.config import BACKUPS_DIR, DB_PATH, REPORTS_DIR, load_config
from engine.notify import notify
from engine.sources.linkedin_guest import LinkedInGuestSource

app = typer.Typer(add_completion=False, help="LinkedIn job search engine.")


@app.callback()
def _setup(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@app.command()
def run(
    force: Annotated[bool, typer.Option(help="Ignore the minimum days between runs.")] = False,
    max_queries: Annotated[int | None, typer.Option(help="Limit the number of query×region combinations (for testing).")] = None,
    open_report: Annotated[bool, typer.Option("--open/--no-open", help="Open the report when done.")] = False,
    notify_done: Annotated[bool, typer.Option("--notify/--no-notify")] = True,
) -> None:
    """Search for jobs, update the history and generate the reports."""
    cfg = load_config()
    conn = db.connect(DB_PATH)
    now = datetime.now(timezone.utc)
    if not force and not pipeline.should_run(conn, cfg, now):
        typer.echo(f"Last run was less than {cfg.min_days_between_runs:g} days ago; nothing to do.")
        return
    pipeline.backup_db(conn, BACKUPS_DIR, cfg.backups_keep, now)
    source = LinkedInGuestSource(
        posted_within_seconds=cfg.posted_within_seconds,
        max_results=cfg.max_results_per_query,
        min_delay=cfg.min_delay,
        max_delay=cfg.max_delay,
        max_retries=cfg.max_retries,
    )
    try:
        run_id = pipeline.run(conn, source, cfg, max_queries=max_queries)
    except Exception as exc:
        if notify_done:
            notify("LinkedIn engine", f"Run failed: {exc}")
        raise
    path = report.render_new_jobs(conn, run_id, REPORTS_DIR)
    report.render_trends(conn, REPORTS_DIR)
    n_new = conn.execute("SELECT n_new FROM runs WHERE id = ?", (run_id,)).fetchone()["n_new"]
    typer.echo(f"{n_new} new relevant jobs → {path}")
    if notify_done:
        notify("LinkedIn engine", f"{n_new} new relevant jobs", REPORTS_DIR / "latest.html")
    if open_report:
        subprocess.run(["open", str(path)], check=False)


@app.command("report")
def report_cmd(run_id: Annotated[int | None, typer.Argument(help="Run id (defaults to the latest).")] = None) -> None:
    """Regenerate the new-jobs report for a run."""
    conn = db.connect(DB_PATH)
    if run_id is None:
        row = conn.execute("SELECT id FROM runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            raise typer.Exit("No runs yet.")
        run_id = row["id"]
    typer.echo(report.render_new_jobs(conn, run_id, REPORTS_DIR))


@app.command()
def rescore() -> None:
    """Re-apply filters, skills and relevance to the whole history (after changing the config)."""
    cfg = load_config()
    conn = db.connect(DB_PATH)
    pipeline.backup_db(conn, BACKUPS_DIR, cfg.backups_keep, datetime.now(timezone.utc))
    typer.echo(dict(pipeline.rescore(conn, cfg)))
    row = conn.execute("SELECT id FROM runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        report.render_new_jobs(conn, row["id"], REPORTS_DIR)
    typer.echo(report.render_trends(conn, REPORTS_DIR))


@app.command()
def trends() -> None:
    """Regenerate the skill trends page."""
    typer.echo(report.render_trends(db.connect(DB_PATH), REPORTS_DIR))


@app.command()
def backup() -> None:
    """Manually back up the database."""
    cfg = load_config()
    typer.echo(pipeline.backup_db(db.connect(DB_PATH), BACKUPS_DIR, cfg.backups_keep, datetime.now(timezone.utc)))
