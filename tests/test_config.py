from engine.config import deep_merge, load_config

BASE = {"queries": ["A"], "search": {"max_results_per_query": 100, "max_retries": 4}}


def test_deep_merge_replaces_lists_and_merges_dicts():
    out = deep_merge(BASE, {"queries": ["B", "C"], "search": {"max_results_per_query": 20}})
    assert out == {"queries": ["B", "C"], "search": {"max_results_per_query": 20, "max_retries": 4}}
    assert BASE["queries"] == ["A"]  # base is not mutated


def test_local_config_overrides_defaults(tmp_path):
    local = tmp_path / "config.local.yaml"
    local.write_text(
        "queries: [Data Engineer]\n"
        "regions: {DE: 101282230}\n"
        "search: {max_results_per_query: 30}\n"
        "scoring: {domain_skill_categories: [data_engineering]}\n"
    )
    skills = tmp_path / "skills.local.yaml"
    skills.write_text("data_engineering:\n  dbt: null\n  Dagster: null\n")
    cfg = load_config(local=local, skills_local=skills)
    assert cfg.queries == ["Data Engineer"]
    assert cfg.regions == {"DE": 101282230}
    assert cfg.max_results_per_query == 30 and cfg.max_retries == 4
    assert cfg.scoring.is_domain("data_engineering") and not cfg.scoring.is_domain("geo_libs")
    assert "Dagster" in cfg.skills["data_engineering"] and "geo_libs" in cfg.skills


def test_missing_local_config_uses_defaults(tmp_path):
    cfg = load_config(local=tmp_path / "nope.yaml", skills_local=None)
    assert "Geospatial Engineer" in cfg.queries


def test_example_local_config_loads():
    from engine.config import LOCAL_CONFIG_EXAMPLE

    cfg = load_config(local=LOCAL_CONFIG_EXAMPLE, skills_local=None)
    assert cfg.queries and cfg.regions and cfg.scoring.threshold == 0
