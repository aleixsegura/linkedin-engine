import pytest

from engine import analysis
from engine.config import load_config


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def skills(text, cfg):
    return analysis.extract_skills(text, cfg)


def test_skill_basic(cfg):
    s = skills("Experience with PostGIS, GeoPandas and ArcGIS Pro. Python and SQL.", cfg)
    assert {"PostGIS", "GeoPandas", "ArcGIS Pro", "Python", "SQL"} <= s.keys()
    assert s["PostGIS"] == "geo_spatial_db"


def test_ambiguous_single_letters(cfg):
    assert "R" not in skills("Our R&D team builds great products", cfg)
    assert "R" in skills("Proficiency in Python or R for analysis", cfg)
    assert "Go" not in skills("We go above and beyond. Let's go!", cfg)
    assert "Go" in skills("Backend services written in Go and Rust", cfg)
    assert "C++" in skills("Strong C++ skills", cfg)
    assert "Java" not in skills("JavaScript developer", cfg)


def test_remote_sensing_is_not_remote_work(cfg):
    assert analysis.detect_workplace("Remote sensing specialist role, on-site in Madrid", "Madrid", cfg) is None
    assert analysis.detect_workplace("This is a fully remote position", "Spain", cfg) == "remote"
    assert analysis.detect_workplace("Hybrid, 2 days remote per week", "London", cfg) == "hybrid"


@pytest.mark.parametrize("title", [
    "GIS Analyst", "Senior GIS Technician", "Técnico GIS", "GIS Intern", "Prácticas Desarrollador GIS",
    "Land Surveyor", "Geospatial Sales Engineer", "Werkstudent GIS",
])
def test_title_excluded(cfg, title):
    assert analysis.title_excluded(title, cfg)


@pytest.mark.parametrize("title", ["Geospatial Software Engineer", "GIS Developer", "Internal Tools Engineer"])
def test_title_not_excluded(cfg, title):
    assert not analysis.title_excluded(title, cfg)


EN = "We build geospatial data pipelines in Python with PostGIS, GDAL and QGIS on AWS. " * 3


def test_assess_visible(cfg):
    a = analysis.assess("Geospatial Data Engineer", "London", EN, cfg)
    assert a.hidden_reason is None and a.lang == "en" and a.relevance >= cfg.scoring.threshold


def test_assess_foreign_language_requirement(cfg):
    a = analysis.assess("GIS Developer", "Berlin", EN + " Fluent German is required.", cfg)
    assert a.hidden_reason == "lang"


def test_assess_description_in_german(cfg):
    de = ("Wir suchen einen Entwickler für unsere Geodaten-Plattform. Sie arbeiten mit "
          "PostGIS und Python in einem agilen Team und entwickeln neue Funktionen. ") * 3
    assert analysis.assess("GIS Developer", "Berlin", de, cfg).hidden_reason == "lang"


def test_assess_clearance(cfg):
    a = analysis.assess("GIS Engineer", "Denver, CO", EN + " Must have an active TS/SCI clearance.", cfg)
    assert a.hidden_reason == "clearance"
    a = analysis.assess("GIS Engineer", "Reston, VA", EN + " Applicants must be a U.S. citizen.", cfg)
    assert a.hidden_reason == "clearance"


def test_no_sponsorship_flag_only(cfg):
    a = analysis.assess("GIS Engineer", "London", EN + " We are unable to offer visa sponsorship.", cfg)
    assert a.no_sponsorship and a.hidden_reason is None


def test_low_score(cfg):
    text = "We build React front-ends for e-commerce with TypeScript. " * 4
    assert analysis.assess("Front-End Engineer", "Madrid", text, cfg).hidden_reason == "low_score"


def test_generic_title_with_geo_skills_is_relevant(cfg):
    assert analysis.assess("Software Engineer", "Barcelona", EN, cfg).hidden_reason is None


def test_seniority_not_penalised(cfg):
    a = analysis.relevance("Principal Geospatial Engineer", {}, cfg)
    b = analysis.relevance("Geospatial Engineer", {}, cfg)
    assert a == b


def test_normalize_title_for_reposts():
    assert analysis.normalize_title("GIS Developer (m/w/d)") == analysis.normalize_title("GIS Developer - Remote")
    assert analysis.normalize_company("Acme Ltd.") == analysis.normalize_company("ACME")


def test_region_for():
    assert analysis.region_for("es", "EU") == "ES"
    assert analysis.region_for("de", "EU") == "EU"
    assert analysis.region_for(None, "US") == "US"


def test_tuning_regressions(cfg):
    assert analysis.title_excluded("Beca Dpto. GIS", cfg)
    assert analysis.relevance("Ingeniero geotécnico", {}, cfg) == analysis.relevance("Ingeniero civil", {}, cfg)
    text = EN + " We strengthen the public trust in data."
    assert analysis.assess("GIS Engineer", "Austin, TX", text, cfg).hidden_reason is None
    civil = "Diseño con AutoCAD, Civil 3D, BIM y GPS para obra civil. " * 4
    assert analysis.assess("Ingeniero/a civil", "Madrid", civil, cfg).hidden_reason == "low_score"
