"""The Dagster asset graph: it loads, the charts depend on real dbt models, and the whole
graph runs end to end on the sample data (tests/sample_data.py)."""

import pytest
from dagster import AssetKey, Definitions, materialize
from dagster_dbt import DbtCliResource
from sample_data import write_sample_raw_data

from semaglutide_spread.definitions import CHARTS, Charts, dbt_models, dbt_project, defs


def test_definitions_load():
    Definitions.validate_loadable(defs)


def test_every_chart_reads_a_dbt_model():
    # A typo in a chart's dependencies would otherwise show up as a new, empty asset.
    for chart in CHARTS:
        for dep in chart.dependency_keys:
            assert dep in dbt_models.keys, f"{chart.key.to_user_string()} depends on {dep}"


def test_dbt_sources_are_upstream_of_the_models():
    graph = defs.resolve_asset_graph()
    staging = graph.get(AssetKey("stg_partd__prescriber_drugs"))
    assert AssetKey(["partd", "by_provider_and_drug"]) in staging.parent_keys


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    data_dir = write_sample_raw_data(tmp_path_factory.mktemp("data"))
    images_dir = tmp_path_factory.mktemp("images")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SEMAGLUTIDE_DATA_DIR", str(data_dir))
        result = materialize(
            [dbt_models, *CHARTS],
            resources={
                "dbt": DbtCliResource(project_dir=dbt_project),
                "charts": Charts(
                    warehouse_path=str(data_dir / "warehouse.duckdb"),
                    images_dir=str(images_dir),
                ),
            },
            raise_on_error=False,
        )
    return result, images_dir


def test_everything_builds_on_the_sample_data(run):
    result, _ = run
    assert result.success


def test_every_chart_is_written(run):
    _, images_dir = run
    names = {p.name for p in images_dir.iterdir()}
    assert {
        "us_uk_night_lights.gif",
        "us_night_lights.gif",
        "uk_night_lights.gif",
        "social_preview.png",
        "us_adoption_curve_light.png",
        "us_uk_comparison_dark.png",
        "us_unexpected_specialties_light.png",
    } <= names


def test_dbt_tests_run_as_asset_checks(run):
    result, _ = run
    checks = result.get_asset_check_evaluations()
    assert len(checks) > 100
    assert all(check.passed for check in checks)
