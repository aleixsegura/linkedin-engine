import json
import math

import pytest
import yaml
from starlette.testclient import TestClient

from engine.config import load_config
from engine.sources.base import JobCard, JobDetail
from engine.web import config_from_form, create_app, save_profile

GEO = "Build geospatial pipelines with Python, PostGIS, GDAL and QGIS. Experience with AWS. " * 3
FORM = {
    "queries": [" Data Engineer ", "data engineer", "GIS Developer", ""],
    "regions": [{"label": "DE", "geo_id": "101282230"}, {"label": "ES", "geo_id": 105646813}],
    "max_results": 25,
    "posted_within_days": 1,
}


@pytest.fixture
def base():
    return load_config(local=None, skills_local=None)


def test_config_from_form(base):
    cfg = config_from_form(base, FORM)
    assert cfg.queries == ["Data Engineer", "GIS Developer"]  # trimmed, deduplicated, blanks dropped
    assert cfg.regions == {"DE": 101282230, "ES": 105646813}
    assert cfg.max_results_per_query == 25 and cfg.posted_within_seconds == 86400
    assert cfg.scoring.threshold == base.scoring.threshold and cfg.title_exclude == base.title_exclude
    assert base.queries != cfg.queries  # the base config is not mutated


def test_config_from_form_without_profile_filters(base):
    cfg = config_from_form(base, {**FORM, "use_profile_filters": False})
    assert cfg.title_exclude == [] and cfg.scoring.threshold == -math.inf
    assert base.scoring.threshold == 4 and base.title_exclude


@pytest.mark.parametrize("change", [
    {"queries": []},
    {"queries": ["  "]},
    {"regions": []},
    {"regions": [{"label": "Spain", "geo_id": "abc"}]},
    {"regions": [{"label": "", "geo_id": "1"}]},
    {"max_results": 0},
    {"max_results": "lots"},
    {"posted_within_days": 90},
])
def test_config_from_form_rejects_invalid_values(base, change):
    with pytest.raises(ValueError):
        config_from_form(base, {**FORM, **change})


def test_save_profile_keeps_other_keys(base, tmp_path):
    local = tmp_path / "config.local.yaml"
    local.write_text("queries: [Old]\nsearch: {min_delay_seconds: 2}\nscoring: {domain_skill_categories: [x]}\n")
    save_profile(config_from_form(base, {**FORM, "use_profile_filters": False}), local)
    raw = yaml.safe_load(local.read_text())
    assert raw["queries"] == ["Data Engineer", "GIS Developer"]
    assert raw["regions"] == {"DE": 101282230, "ES": 105646813}
    assert raw["search"] == {"min_delay_seconds": 2, "max_results_per_query": 25, "posted_within_seconds": 86400}
    assert raw["scoring"] == {"domain_skill_categories": ["x"], "threshold": -math.inf}
    cfg = load_config(local=local, skills_local=None)
    assert cfg.queries == ["Data Engineer", "GIS Developer"] and cfg.title_exclude == []


class FakeSource:
    name = "fake"

    def search(self, query, region, geo_id):
        if region == "ES":
            yield JobCard("1", "fake", "Geospatial Engineer", "Acme", "Madrid, Spain", "es", "https://x/1", "2026-10-25")
            yield JobCard("2", "fake", "GIS Analyst", "Acme", "Madrid, Spain", "es", "https://x/2", "2026-10-25")

    def detail(self, job_id):
        return JobDetail(GEO, "Mid-Senior level", "Full-time")


@pytest.fixture
def client(tmp_path, base):
    app = create_app(
        db_path=tmp_path / "jobs.db", reports_dir=tmp_path / "reports", backups_dir=tmp_path / "backups",
        local_config=tmp_path / "config.local.yaml", load=lambda: load_config(local=None, skills_local=None),
        source_factory=lambda cfg: FakeSource(),
    )
    return TestClient(app, base_url="http://127.0.0.1")


def _events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_index_page(client):
    html = client.get("/").text
    assert "Find jobs" in html and "Geospatial Engineer" in html and "JobsView" in html


def test_search_streams_events_and_writes_reports(client, tmp_path):
    form = {**FORM, "queries": ["Geospatial Engineer"], "regions": [{"label": "ES", "geo_id": 105646813}],
            "save": True}
    with client.stream("POST", "/api/search", json=form) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        events = _events(resp.read().decode())
    types = [e["type"] for e in events]
    assert types[0] == "progress" and types[-1] == "done"
    job = next(e["job"] for e in events if e["type"] == "job")
    assert job["title"] == "Geospatial Engineer" and any(s["name"] == "PostGIS" and s["domain"] for s in job["skills"])
    assert [e["reason"] for e in events if e["type"] == "hidden"] == ["title"]
    assert events[-1]["n_new"] == 1
    assert "Geospatial Engineer" in client.get("/reports/latest.html").text
    assert client.get("/reports/trends.html").status_code == 200
    assert yaml.safe_load((tmp_path / "config.local.yaml").read_text())["queries"] == ["Geospatial Engineer"]


def test_search_rejects_invalid_requests(client):
    assert client.post("/api/search", json={**FORM, "queries": []}).status_code == 400
    assert client.post("/api/search", content=json.dumps(FORM), headers={"Content-Type": "text/plain"}).status_code == 415


def test_one_search_at_a_time_and_cancel(tmp_path):
    import threading

    started, release = threading.Event(), threading.Event()

    class Slow(FakeSource):
        def search(self, query, region, geo_id):
            started.set()
            release.wait(5)
            yield from super().search(query, region, geo_id)

    app = create_app(
        db_path=tmp_path / "jobs.db", reports_dir=tmp_path / "reports", backups_dir=tmp_path / "backups",
        local_config=tmp_path / "config.local.yaml", load=lambda: load_config(local=None, skills_local=None),
        source_factory=lambda cfg: Slow(),
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    with client.stream("POST", "/api/search", json=FORM) as first:
        assert first.status_code == 200
        assert started.wait(5)
        assert client.post("/api/search", json=FORM).status_code == 409
        assert client.post("/api/cancel").status_code == 204
        release.set()
        events = _events(first.read().decode())
    assert events[-1]["type"] == "cancelled"
    with client.stream("POST", "/api/search", json=FORM) as again:  # the slot is free again
        assert _events(again.read().decode())[-1]["type"] == "done"


def test_rejects_foreign_hosts(client):
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
