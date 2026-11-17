"""The Dagster asset graph: it loads, every dbt source has a downloader upstream, the charts
depend on real dbt models, and everything after the downloads runs end to end on the sample
data (tests/sample_data.py). The downloaders are stubbed out: nothing here touches the network."""

import pytest
from dagster import AssetKey, Definitions, materialize
from dagster_dbt import DbtCliResource
from sample_data import write_sample_raw_data

from semaglutide_spread import paths
from semaglutide_spread.definitions import (
    CHARTS,
    Charts,
    dbt_models,
    dbt_project,
    defs,
    monthly_refresh,
)
from semaglutide_spread.sources import ods, partd
from semaglutide_spread.sources.assets import DOWNLOADS


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


def test_every_dbt_source_has_a_downloader():
    # A dbt source with no downloader shows up in the graph as an asset nothing can build.
    graph = defs.resolve_asset_graph()
    unbuildable = [k for k in graph.get_all_asset_keys() if not graph.get(k).is_materializable]
    assert unbuildable == []


def test_lookups_run_after_the_files_they_read():
    graph = defs.resolve_asset_graph()
    assert (
        AssetKey(["partd", "by_provider"])
        in graph.get(AssetKey(["census", "geocoder"])).parent_keys
    )
    assert (
        AssetKey(["wales", "register"])
        in graph.get(AssetKey(["postcodes_io", "postcodes"])).parent_keys
    )


def download(name: str):
    return next(d for d in DOWNLOADS if d.op.name == f"download_{name}")


def test_a_download_records_what_it_saved(tmp_path, monkeypatch):
    def fake_main(argv):
        out = tmp_path / "ods_gp_practices"
        out.mkdir()
        (out / "data.parquet").write_bytes(b"x" * 2_000_000)
        return 0

    monkeypatch.setattr(paths, "RAW_DIR", tmp_path)
    monkeypatch.setattr(ods, "main", fake_main)
    result = materialize([download("ods")])
    assert result.success
    [event] = result.get_asset_materialization_events()
    metadata = event.materialization.metadata
    assert metadata["files"].value == 1
    assert metadata["size_mb"].value == 2.0


def test_a_failed_download_fails_the_run(monkeypatch):
    monkeypatch.setattr(partd, "main", lambda argv: 1)
    result = materialize([download("partd")], raise_on_error=False)
    assert not result.success


def test_the_monthly_schedule_refreshes_everything_and_starts_off():
    assert monthly_refresh.cron_schedule == "0 6 20 * *"
    assert monthly_refresh.default_status.value == "STOPPED"
    graph = defs.resolve_asset_graph()
    selected = monthly_refresh.job.selection.resolve(graph)
    assert selected == graph.get_all_asset_keys()


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
