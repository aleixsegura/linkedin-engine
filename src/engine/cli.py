"""CLI: `engine run | ui | report | trends | backup`."""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import threading
import webbrowser
from datetime import datetime, timezone
from typing import Annotated

import typer

from engine import db, pipeline, report
from engine.config import (
    BACKUPS_DIR,
    DB_PATH,
    LOCAL_CONFIG,
    LOCAL_CONFIG_EXAMPLE,
    REPORTS_DIR,
    Config,
    load_config,
)
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


def _parse_region(value: str) -> tuple[str, int]:
    name, sep, geo_id = value.partition("=")
    if not sep or not name.strip() or not geo_id.strip().isdigit():
        raise typer.BadParameter(f"expected NAME=GEOID (e.g. ES=105646813), got {value!r}")
    return name.strip(), int(geo_id)


def _apply_overrides(
    cfg: Config, queries: list[str] | None, regions: list[str] | None, max_results: int | None
) -> None:
    if queries:
        cfg.queries = queries
    if regions:
        cfg.regions = dict(_parse_region(r) for r in regions)
    if max_results is not None:
        cfg.max_results_per_query = max_results


@app.command()
def init(force: Annotated[bool, typer.Option(help="Overwrite an existing local config.")] = False) -> None:
    """Create config/config.local.yaml, your personal search profile (roles, regions, limits)."""
    if LOCAL_CONFIG.exists() and not force:
        raise typer.Exit(f"{LOCAL_CONFIG} already exists (use --force to overwrite).")
    shutil.copyfile(LOCAL_CONFIG_EXAMPLE, LOCAL_CONFIG)
    typer.echo(f"Created {LOCAL_CONFIG}. Edit it to set your queries, regions and limits.")


@app.command("config")
def show_config() -> None:
    """Show the effective search settings (defaults merged with your local config)."""
    cfg = load_config()
    typer.echo(f"Local config: {LOCAL_CONFIG if LOCAL_CONFIG.exists() else 'none (using defaults)'}")
    typer.echo("Queries:\n" + "\n".join(f"  - {q}" for q in cfg.queries))
    typer.echo("Regions:\n" + "\n".join(f"  - {r}: {g}" for r, g in cfg.regions.items()))
    typer.echo(f"Max results per query × region: {cfg.max_results_per_query}")
    typer.echo(f"Posted within: {cfg.posted_within_seconds // 86400} days")
    typer.echo(f"Relevance threshold: {cfg.scoring.threshold:g}")


@app.command()
def run(
    force: Annotated[bool, typer.Option(help="Ignore the minimum days between runs.")] = False,
    query: Annotated[
        list[str] | None, typer.Option("--query", "-q", help="Search query (repeatable). Overrides the config.")
    ] = None,
    region: Annotated[
        list[str] | None,
        typer.Option("--region", "-r", help="Region as NAME=GEOID (repeatable). Overrides the config."),
    ] = None,
    max_results: Annotated[
        int | None, typer.Option(min=1, help="Max jobs fetched per query × region. Overrides the config.")
    ] = None,
    max_queries: Annotated[int | None, typer.Option(help="Limit the number of query×region combinations (for testing).")] = None,
    open_report: Annotated[bool, typer.Option("--open/--no-open", help="Open the report when done.")] = False,
    notify_done: Annotated[bool, typer.Option("--notify/--no-notify")] = True,
) -> None:
    """Search for jobs, update the history and generate the reports."""
    cfg = load_config()
    _apply_overrides(cfg, query, region, max_results)
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
    path = report.render_new_jobs(conn, run_id, REPORTS_DIR, cfg.scoring)
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
    typer.echo(report.render_new_jobs(conn, run_id, REPORTS_DIR, load_config().scoring))


@app.command()
def rescore() -> None:
    """Re-apply filters, skills and relevance to the whole history (after changing the config)."""
    cfg = load_config()
    conn = db.connect(DB_PATH)
    pipeline.backup_db(conn, BACKUPS_DIR, cfg.backups_keep, datetime.now(timezone.utc))
    typer.echo(dict(pipeline.rescore(conn, cfg)))
    row = conn.execute("SELECT id FROM runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        report.render_new_jobs(conn, row["id"], REPORTS_DIR, cfg.scoring)
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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="Port to listen on (default: a free one).")] = 0,
    browser: Annotated[bool, typer.Option("--browser/--no-browser", help="Open the page in the browser.")] = True,
) -> None:
    """Start the local web UI (only reachable from this computer) and open it in the browser."""
    import uvicorn

    from engine.web import create_app

    port = port or _free_port()
    url = f"http://127.0.0.1:{port}/"
    typer.echo(f"linkedin-engine is running at {url}\nKeep this window open while you use it; press Ctrl+C to stop.")
    if browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
