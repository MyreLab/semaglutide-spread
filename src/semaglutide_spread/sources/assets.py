"""The downloaders as Dagster assets.

Each downloader is one asset per raw table it writes, keyed like the dbt source that reads
it (e.g. `partd/by_provider_and_drug`), so the dbt staging models depend on them directly and
the lineage runs from the publishers' APIs to the charts.

Running an asset runs the downloader's command-line entry point with its defaults: every
release from 2018 on, skipping anything already saved and unchanged. HTTP retries happen in
the downloaders themselves (see common.py), so the assets don't add their own.
"""

# No `from __future__ import annotations` here: Dagster reads the type hints at runtime.

from collections.abc import Iterator, Sequence
from types import ModuleType

from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetsDefinition,
    AssetSpec,
    Failure,
    MaterializeResult,
    multi_asset,
)

from semaglutide_spread import paths
from semaglutide_spread.sources import census, epd, geocoder, ni, ods, partd, phs, postcodes, wales


def raw_folder(key: AssetKey) -> str:
    """The folder under data/raw a source table is saved in."""
    source, table = key.path
    return "postcodes" if source == "postcodes_io" else f"{source}_{table}"


def folder_metadata(key: AssetKey) -> dict:
    """How many Parquet files the folder holds and how big they are, for the Dagster UI."""
    files = list((paths.RAW_DIR / raw_folder(key)).rglob("*.parquet"))
    size = sum(f.stat().st_size for f in files)
    return {"files": len(files), "size_mb": round(size / 1e6, 1)}


def downloader(
    module: ModuleType,
    tables: Sequence[str],
    *,
    source: str | None = None,
    deps: Sequence[AssetKey] = (),
) -> AssetsDefinition:
    """One multi-asset for a downloader: an asset per table, all written by one run."""
    name = module.__name__.rsplit(".", 1)[-1]
    source = source or name
    description = (module.__doc__ or "").strip().split("\n")[0]
    specs = [
        AssetSpec(
            AssetKey([source, table]), deps=deps, group_name="downloads", description=description
        )
        for table in tables
    ]

    @multi_asset(name=f"download_{name}", specs=specs, can_subset=False)
    def _download(context: AssetExecutionContext) -> Iterator[MaterializeResult]:
        status = module.main([])
        if status != 0:
            raise Failure(f"{name} downloader exited with status {status}")
        for spec in specs:
            yield MaterializeResult(asset_key=spec.key, metadata=folder_metadata(spec.key))

    return _download


def key(source: str, table: str) -> AssetKey:
    return AssetKey([source, table])


DOWNLOADS = [
    downloader(partd, ["by_provider_and_drug", "by_provider"]),
    downloader(census, ["zcta2020_gazetteer", "zcta2020_counties"]),
    # Geocodes the prescriber addresses whose ZIP code isn't a Census ZIP area, so it needs
    # both of those first.
    downloader(
        geocoder,
        ["geocoder"],
        source="census",
        deps=[key("partd", "by_provider"), key("census", "zcta2020_gazetteer")],
    ),
    downloader(epd, ["diabetes", "practices"]),
    downloader(ods, ["gp_practices"]),
    downloader(phs, ["diabetes", "practices", "register"]),
    downloader(ni, ["diabetes", "practices", "register"]),
    downloader(wales, ["diabetes", "practices", "register"]),
    # Looks up every practice postcode the four nations' files mention.
    downloader(
        postcodes,
        ["postcodes"],
        source="postcodes_io",
        deps=[
            key("epd", "practices"),
            key("phs", "register"),
            key("ni", "register"),
            key("wales", "register"),
        ],
    ),
]
