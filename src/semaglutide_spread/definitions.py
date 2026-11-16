"""Dagster definitions: the dbt models and the charts, as one asset graph.

Every dbt model, seed and test is loaded from the dbt project, and each chart is an asset
downstream of the tables it reads, so Dagster knows what to rebuild when a model changes.

Usage:
    uv run dagster dev                               # the UI, at http://localhost:3000
    uv run dagster asset materialize --select '*'    # build everything from the command line
"""

# No `from __future__ import annotations` here: Dagster reads the type hints at runtime.

import os
from pathlib import Path

import duckdb
from dagster import (
    AssetExecutionContext,
    AssetKey,
    ConfigurableResource,
    Definitions,
    MaterializeResult,
    MetadataValue,
    asset,
)
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

from semaglutide_spread.paths import DATA_DIR, PROJECT_ROOT, WAREHOUSE_PATH
from semaglutide_spread.viz import (
    adoption_curve,
    combined_night_lights,
    comparison,
    night_lights,
    social_preview,
    specialties,
)

# dbt's profile reads the warehouse location from this variable (see dbt/profiles.yml).
os.environ.setdefault("SEMAGLUTIDE_DATA_DIR", str(DATA_DIR))

dbt_project = DbtProject(
    project_dir=PROJECT_ROOT / "dbt",
    profiles_dir=PROJECT_ROOT / "dbt",
    # The project has no dbt packages, so there's nothing for `dbt deps` to install.
    install_deps=False,
)
# Parse the dbt project on every load, so the asset graph always matches the SQL. It takes a
# few seconds; a deployed setup would build the manifest once, at deploy time, instead.
dbt_project.preparer.prepare(dbt_project)


class LayerGroups(DagsterDbtTranslator):
    """Group dbt assets by layer (staging, intermediate, marts, reports, seeds), so the
    lineage graph in the UI reads left to right the way the dbt project is laid out."""

    def get_group_name(self, dbt_resource_props):
        if dbt_resource_props["resource_type"] == "seed":
            return "seeds"
        return dbt_resource_props["fqn"][1]  # [project, folder, ..., name]


@dbt_assets(manifest=dbt_project.manifest_path, dagster_dbt_translator=LayerGroups())
def dbt_models(context: AssetExecutionContext, dbt: DbtCliResource):
    """`dbt build` for the selected models: each model is built, then its tests run."""
    yield from dbt.cli(["build"], context=context).stream()


# --- Charts --------------------------------------------------------------------------------


class Charts(ConfigurableResource):
    """Where the charts read from and write to. Tests point both at temporary folders."""

    warehouse_path: str = str(WAREHOUSE_PATH)
    images_dir: str = str(night_lights.IMAGES_DIR)

    def connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(self.warehouse_path, read_only=True)

    @property
    def out_dir(self) -> Path:
        return Path(self.images_dir)


def tables(*names: str) -> list[AssetKey]:
    return [AssetKey(name) for name in names]


US_MAP = tables("us_adopters", "us_population_at_risk", "rpt_us_adoption_curve")
UK_MAP = tables("uk_adopters", "uk_population_at_risk", "rpt_uk_adoption_curve")


def written(paths: list[Path]) -> MaterializeResult:
    """Record which files an asset wrote, and their sizes, in the Dagster UI."""
    return MaterializeResult(
        metadata={p.name: MetadataValue.text(f"{p.stat().st_size / 1e6:.2f} MB") for p in paths}
    )


@asset(group_name="charts", deps=US_MAP + UK_MAP)
def night_lights_maps(charts: Charts) -> MaterializeResult:
    """One night-lights GIF per country, and a still of each map's last frame."""
    with charts.connect() as con:
        paths = [
            path
            for country in night_lights.COUNTRIES.values()
            for path in night_lights.render(con, country, out_dir=charts.out_dir)
        ]
    return written(paths)


@asset(group_name="charts", deps=US_MAP + UK_MAP)
def combined_night_lights_map(charts: Charts) -> MaterializeResult:
    """Both countries' night-lights maps in one GIF, on one clock: the README's lead image."""
    with charts.connect() as con:
        return written([combined_night_lights.render(con, out_dir=charts.out_dir)])


@asset(group_name="charts", deps=US_MAP + UK_MAP)
def social_preview_image(charts: Charts) -> MaterializeResult:
    """The image GitHub shows when the repo is shared."""
    with charts.connect() as con:
        return written([social_preview.render(con, out_dir=charts.out_dir)])


@asset(group_name="charts", deps=tables("rpt_us_adoption_curve", "rpt_uk_adoption_curve"))
def adoption_curves(charts: Charts) -> MaterializeResult:
    """Adoption curves for the US, the UK, and each UK nation."""
    with charts.connect() as con:
        return written(adoption_curve.render(con, out_dir=charts.out_dir))


@asset(
    group_name="charts",
    deps=tables(
        "rpt_us_adoption_curve",
        "rpt_uk_adoption_curve",
        "rpt_us_prescription_share",
        "rpt_uk_prescription_share",
    ),
)
def us_uk_comparison(charts: Charts) -> MaterializeResult:
    """US vs UK: adoption against years since the first adopters, and share of prescriptions."""
    with charts.connect() as con:
        return written(comparison.render(con, out_dir=charts.out_dir))


@asset(group_name="charts", deps=tables("rpt_us_adopters_by_specialty"))
def unexpected_specialties(charts: Charts) -> MaterializeResult:
    """US adopters from specialties that rarely manage diabetes."""
    with charts.connect() as con:
        return written(specialties.render(con, out_dir=charts.out_dir))


CHARTS = [
    night_lights_maps,
    combined_night_lights_map,
    social_preview_image,
    adoption_curves,
    us_uk_comparison,
    unexpected_specialties,
]

defs = Definitions(
    assets=[dbt_models, *CHARTS],
    resources={"dbt": DbtCliResource(project_dir=dbt_project), "charts": Charts()},
)
