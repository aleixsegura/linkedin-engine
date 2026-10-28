from pathlib import Path

from engine.sources.linkedin_guest import parse_detail, parse_search

FIX = Path(__file__).parent / "fixtures"


def test_parse_search_uk():
    cards = parse_search((FIX / "search_uk.html").read_text(), "UK")
    assert len(cards) == 10
    c = cards[0]
    assert c.id.isdigit() and c.title and c.company and c.location
    assert c.country == "uk"
    assert c.url.startswith("https://uk.linkedin.com/jobs/view/") and "?" not in c.url
    assert c.posted_at and len(c.posted_at) == 10


def test_parse_search_eu_country_from_subdomain():
    cards = parse_search((FIX / "search_eu.html").read_text(), "EU")
    assert cards and cards[0].country == "de"


def test_parse_detail_en():
    d = parse_detail((FIX / "detail_en.html").read_text())
    assert d is not None
    assert "PostGIS" in d.description
    assert d.employment_type == "Full-time"
    assert d.seniority == "Not Applicable"


def test_parse_detail_missing_description():
    assert parse_detail("<html><body>nothing</body></html>") is None
