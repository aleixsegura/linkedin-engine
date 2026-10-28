from datetime import datetime, timedelta, timezone

import pytest

from engine import db, pipeline, report
from engine.config import load_config
from engine.sources.base import JobCard, JobDetail

GEO = "Build geospatial pipelines with Python, PostGIS, GDAL and QGIS. Experience with AWS. " * 3


class FakeSource:
    name = "fake"

    def __init__(self, cards, details):
        self.cards, self.details, self.detail_calls = cards, details, []

    def search(self, query, region, geo_id):
        yield from self.cards.get(region, [])

    def detail(self, job_id):
        self.detail_calls.append(job_id)
        return self.details.get(job_id)


def card(id, title, company="Acme", country="es"):
    return JobCard(id, "fake", title, company, "Madrid, Spain", country, f"https://x/{id}", "2026-10-25")


@pytest.fixture
def cfg():
    c = load_config()
    c.queries = ["Geospatial Engineer"]
    return c


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "jobs.db")


def test_full_run_and_reports(conn, cfg, tmp_path):
    src = FakeSource(
        {"ES": [card("1", "Geospatial Engineer"), card("2", "GIS Analyst")],
         "EU": [card("1", "Geospatial Engineer"), card("3", "Front-End Engineer", "Shop", "de")]},
        {"1": JobDetail(GEO, "Mid-Senior level", "Full-time"),
         "3": JobDetail("React and TypeScript for our web shop frontend. " * 4, None, None)},
    )
    run_id = pipeline.run(conn, src, cfg)
    jobs = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM jobs")}
    assert jobs["1"]["hidden_reason"] is None and jobs["1"]["region"] == "ES"
    assert jobs["2"]["hidden_reason"] == "title"
    assert jobs["3"]["hidden_reason"] == "low_score" and jobs["3"]["region"] == "EU"
    assert "2" not in src.detail_calls  # excluded titles don't fetch details
    regions = {r["region"] for r in conn.execute("SELECT region FROM job_queries WHERE job_id = '1'")}
    assert regions == {"ES", "EU"}
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "ok" and run["n_new"] == 1

    html = report.render_new_jobs(conn, run_id, tmp_path).read_text()
    assert "Geospatial Engineer" in html and "Front-End Engineer" not in html and "PostGIS" in html
    data = report.trend_dataset(conn)
    assert len(data["jobs"]) == 1  # only the relevant job counts
    assert "const DATA = " in report.render_trends(conn, tmp_path).read_text()


def test_second_run_only_new_and_reposts(conn, cfg):
    first = FakeSource({"ES": [card("1", "Geospatial Engineer")]}, {"1": JobDetail(GEO, None, None)})
    pipeline.run(conn, first, cfg)
    second = FakeSource(
        {"ES": [card("1", "Geospatial Engineer"), card("9", "Geospatial Engineer (m/f/d)")]},
        {"9": JobDetail(GEO, None, None)},
    )
    run_id = pipeline.run(conn, second, cfg)
    assert second.detail_calls == ["9"]
    job9 = conn.execute("SELECT * FROM jobs WHERE id = '9'").fetchone()
    assert job9["canonical_id"] == "1"
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["n_new"] == 0 and '"repost": 1' in run["n_hidden_json"]


def test_should_run_guard(conn, cfg):
    now = datetime.now(timezone.utc)
    assert pipeline.should_run(conn, cfg, now)
    pipeline.run(conn, FakeSource({}, {}), cfg)
    assert not pipeline.should_run(conn, cfg, now + timedelta(days=1))
    assert pipeline.should_run(conn, cfg, now + timedelta(days=3, minutes=1))


def test_failed_run_is_recorded(conn, cfg):
    class Boom(FakeSource):
        def search(self, *a):
            raise RuntimeError("blocked")
            yield

    with pytest.raises(RuntimeError):
        pipeline.run(conn, Boom({}, {}), cfg)
    assert conn.execute("SELECT status FROM runs").fetchone()["status"] == "error"


def test_rescore_applies_new_config(conn, cfg):
    pipeline.run(conn, FakeSource({"ES": [card("1", "Geospatial Engineer")]}, {"1": JobDetail(GEO, None, None)}), cfg)
    cfg.title_exclude.append(__import__("re").compile("geospatial", 2))
    out = pipeline.rescore(conn, cfg)
    assert out["title"] == 1
    assert conn.execute("SELECT COUNT(*) FROM job_skills").fetchone()[0] == 0
