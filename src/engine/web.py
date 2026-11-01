"""Local web UI: `engine ui` serves a search form on 127.0.0.1 and streams results as they're found (SSE)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

import yaml
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from engine import db, pipeline, report
from engine.config import BACKUPS_DIR, DB_PATH, LOCAL_CONFIG, REPORTS_DIR, Config, load_config
from engine.sources.base import Source
from engine.sources.linkedin_guest import LinkedInGuestSource

log = logging.getLogger(__name__)

# Label (shown in reports), name (shown in the form) and LinkedIn geoId.
REGION_CATALOG = [
    ("ES", "Spain", 105646813),
    ("EU", "European Union", 91000000),
    ("UK", "United Kingdom", 101165590),
    ("US", "United States", 103644278),
    ("DE", "Germany", 101282230),
    ("FR", "France", 105015875),
    ("NL", "Netherlands", 102890719),
    ("IE", "Ireland", 104738515),
    ("IT", "Italy", 103350119),
    ("PT", "Portugal", 100364837),
    ("CH", "Switzerland", 106693272),
    ("CA", "Canada", 101174742),
    ("WW", "Worldwide", 92000000),
]
ROLE_SUGGESTIONS = [
    "Software Engineer", "Backend Engineer", "Frontend Engineer", "Full Stack Developer", "Data Engineer",
    "Data Scientist", "Analytics Engineer", "Machine Learning Engineer", "DevOps Engineer", "QA Engineer",
    "Product Manager", "UX Designer", "GIS Developer", "Geospatial Engineer",
]
MAX_QUERIES = 20
MAX_REGIONS = 20
MAX_RESULTS = 1000
MAX_DAYS = 30
DAY = 86400
PING_SECONDS = 15


def config_from_form(base: Config, form: dict) -> Config:
    """Apply the form values on top of `base` (defaults + local profile). Raises ValueError if invalid."""
    queries: list[str] = []
    for q in form.get("queries") or []:
        q = str(q).strip()
        if q and len(q) <= 100 and q.lower() not in {x.lower() for x in queries}:
            queries.append(q)
    if not queries:
        raise ValueError("Enter at least one role.")
    if len(queries) > MAX_QUERIES:
        raise ValueError(f"Use at most {MAX_QUERIES} roles.")

    regions: dict[str, int] = {}
    for r in form.get("regions") or []:
        label = str(r.get("label", "")).strip()[:30] if isinstance(r, dict) else ""
        geo_id = str(r.get("geo_id", "")).strip() if isinstance(r, dict) else ""
        if not label or not geo_id.isdigit():
            raise ValueError(f"Invalid region {r!r}: it needs a name and a numeric LinkedIn geoId.")
        regions[label] = int(geo_id)
    if not regions:
        raise ValueError("Choose at least one region.")
    if len(regions) > MAX_REGIONS:
        raise ValueError(f"Use at most {MAX_REGIONS} regions.")

    try:
        max_results = int(form.get("max_results", base.max_results_per_query))
        days = int(form.get("posted_within_days", base.posted_within_seconds // DAY))
    except (TypeError, ValueError):
        raise ValueError("Max results and posted-within days must be whole numbers.") from None
    if not 1 <= max_results <= MAX_RESULTS:
        raise ValueError(f"Max results must be between 1 and {MAX_RESULTS}.")
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"Posted within must be between 1 and {MAX_DAYS} days.")

    cfg = dataclasses.replace(
        base, queries=queries, regions=regions, max_results_per_query=max_results,
        posted_within_seconds=days * DAY,
    )
    if not form.get("use_profile_filters", True):
        # The profile's title exclusions and relevance threshold are tuned for its own roles:
        # for other roles, show everything that passes the language/clearance filters.
        cfg.title_exclude = []
        cfg.scoring = dataclasses.replace(cfg.scoring, threshold=float("-inf"))
    return cfg


def save_profile(cfg: Config, path: Path = LOCAL_CONFIG) -> None:
    """Write the search choices into the local config, keeping any other keys already there."""
    raw = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    raw["queries"] = list(cfg.queries)
    raw["regions"] = dict(cfg.regions)
    search = raw.setdefault("search", {})
    search["max_results_per_query"] = cfg.max_results_per_query
    search["posted_within_seconds"] = cfg.posted_within_seconds
    if not cfg.title_exclude:
        raw["title_exclude"] = []
    if cfg.scoring.threshold == float("-inf"):
        raw.setdefault("scoring", {})["threshold"] = cfg.scoring.threshold
    path.write_text(
        "# Saved from the web UI (`engine ui`). Any key from config/config.yaml can be added here.\n"
        + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    )


def _profile(cfg: Config, has_local: bool) -> dict:
    return {
        "queries": cfg.queries,
        "regions": [{"label": k, "geo_id": v} for k, v in cfg.regions.items()],
        "max_results": cfg.max_results_per_query,
        "posted_within_days": max(1, round(cfg.posted_within_seconds / DAY)),
        "has_local_config": has_local,
    }


def _linkedin_source(cfg: Config) -> Source:
    return LinkedInGuestSource(
        posted_within_seconds=cfg.posted_within_seconds,
        max_results=cfg.max_results_per_query,
        min_delay=cfg.min_delay,
        max_delay=cfg.max_delay,
        max_retries=cfg.max_retries,
    )


def search_events(
    conn, source: Source, cfg: Config, reports_dir: Path, cancel: threading.Event | None = None
) -> Iterator[dict]:
    """Pipeline events turned into UI events: progress, job (with its details), hidden, warning, done."""
    for event in pipeline.run_events(conn, source, cfg, cancel=cancel):
        kind = event.pop("type")
        if kind in ("start", "search"):
            yield {"type": "progress", "stage": kind, **event}
        elif kind == "job" and event["outcome"] == "visible":
            yield {"type": "job", "index": event["index"], "total": event["total"],
                   "job": report.job_views(conn, [event["id"]], cfg.scoring)[0]}
        elif kind == "job":
            reason = event["outcome"]
            yield {"type": "hidden", "index": event["index"], "total": event["total"], "reason": reason,
                   "label": report.HIDDEN_LABELS.get(reason, reason)}
        elif kind == "warning":
            yield {"type": "warning", "message": event["message"]}
        elif kind == "done":
            report.render_new_jobs(conn, event["run_id"], reports_dir, cfg.scoring)
            report.render_trends(conn, reports_dir)
            yield {"type": "done", "run_id": event["run_id"], "n_cards": event["n_cards"], "n_new": event["n_new"],
                   "hidden": {report.HIDDEN_LABELS.get(k, k): n for k, n in event["hidden"].items()},
                   "report_url": "reports/latest.html", "trends_url": "reports/trends.html"}


class _Search:
    """The single search allowed at a time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancel: threading.Event | None = None


def create_app(
    *,
    db_path: Path = DB_PATH,
    reports_dir: Path = REPORTS_DIR,
    backups_dir: Path = BACKUPS_DIR,
    local_config: Path = LOCAL_CONFIG,
    load: Callable[[], Config] = load_config,
    source_factory: Callable[[Config], Source] = _linkedin_source,
) -> Starlette:
    current = _Search()
    reports_dir.mkdir(parents=True, exist_ok=True)

    def worker(cfg: Config, save: bool, cancel: threading.Event, out: queue.Queue) -> None:
        conn = None
        try:
            if save:
                save_profile(cfg, local_config)
            conn = db.connect(db_path)
            pipeline.backup_db(conn, backups_dir, cfg.backups_keep, datetime.now(timezone.utc))
            for event in search_events(conn, source_factory(cfg), cfg, reports_dir, cancel):
                out.put(event)
        except pipeline.Cancelled:
            out.put({"type": "cancelled"})
        except Exception as exc:
            log.exception("Search failed")
            out.put({"type": "error", "message": f"The search failed: {exc}"})
        finally:
            if conn is not None:
                conn.close()
            current.cancel = None
            current.lock.release()
            out.put(None)

    async def index(request: Request) -> HTMLResponse:
        cfg = load()
        bootstrap = {
            "profile": _profile(cfg, local_config.exists()),
            "catalog": [{"label": label, "name": name, "geo_id": g} for label, name, g in REGION_CATALOG],
            "suggestions": ROLE_SUGGESTIONS,
            "has_reports": (reports_dir / "latest.html").exists(),
        }
        html = report._env.get_template("ui.html.j2").render(bootstrap_json=report.script_json(bootstrap))
        return HTMLResponse(html)

    async def search(request: Request) -> Response:
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return JSONResponse({"error": "Expected a JSON body."}, status_code=415)
        try:
            form = await request.json()
            if not isinstance(form, dict):
                raise ValueError("Expected a JSON object.")
            cfg = config_from_form(load(), form)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not current.lock.acquire(blocking=False):
            return JSONResponse({"error": "A search is already running. Wait for it or cancel it."}, status_code=409)
        cancel = current.cancel = threading.Event()
        out: queue.Queue = queue.Queue()
        threading.Thread(target=worker, args=(cfg, bool(form.get("save")), cancel, out), daemon=True).start()

        def next_event() -> dict | None | str:
            try:
                return out.get(timeout=PING_SECONDS)
            except queue.Empty:
                return "ping"

        async def stream():
            try:
                while (event := await asyncio.to_thread(next_event)) is not None:
                    if event == "ping":
                        yield ": ping\n\n"
                    else:
                        yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                cancel.set()  # finished, or the page was closed: stop the worker at its next step

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        )

    async def cancel_search(request: Request) -> Response:
        if current.cancel is not None:
            current.cancel.set()
        return Response(status_code=204)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/search", search, methods=["POST"]),
            Route("/api/cancel", cancel_search, methods=["POST"]),
            Mount("/reports", StaticFiles(directory=reports_dir, html=True), name="reports"),
        ],
        # Only answer requests addressed to this machine (blocks DNS-rebinding from other sites).
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])],
    )
