"""Download Medicare Part D prescriber files from data.cms.gov and store them as Parquet.

Two CMS datasets are used, one release per data year:

- "by Provider and Drug": one row per prescriber x drug (the adoption data)
- "by Provider": one row per prescriber, including ZIP code (the location data)

For each year the CSV is downloaded, checked against the CMS data dictionary, converted to
Parquet with every column kept as text, and the CSV is deleted. Types are assigned later, in
the dbt staging models, so nothing is lost or misread at this stage (for example, the
leading zero in state FIPS code "06").

Re-running is safe and cheap: years already saved are skipped unless CMS has re-published
them, and a newly published year is picked up automatically.

Usage:
    uv run partd-download                      # 2018 to the latest year CMS has published
    uv run partd-download --years 2018-2024
    uv run partd-download --dataset by_provider --years 2024 --force
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from semaglutide_spread.paths import RAW_DIR
from semaglutide_spread.sources.common import (
    check_columns,
    convert_csv,
    download_with_retries,
    get_json,
    new_client,
    read_header,
)

log = logging.getLogger(__name__)

CATALOG_URL = "https://data.cms.gov/data.json"

DATASET_TITLES = {
    "by_provider_and_drug": "Medicare Part D Prescribers - by Provider and Drug",
    "by_provider": "Medicare Part D Prescribers - by Provider",
}

FIRST_YEAR = 2018  # semaglutide (Ozempic) was approved in December 2017


@dataclass(frozen=True)
class Release:
    """One year of one CMS dataset, as listed in the CMS catalog."""

    dataset: str
    year: int
    csv_url: str
    dictionary_url: str
    modified: str


# --- Finding the files ---------------------------------------------------------------------


def find_releases(catalog: dict, dataset: str) -> dict[int, Release]:
    """Pick out every yearly release of `dataset` from the CMS catalog (data.json).

    Each year is its own catalog entry, titled e.g.
    "Medicare Part D Prescribers - by Provider and Drug : 2024-12-01".
    """
    title = DATASET_TITLES[dataset]
    releases: dict[int, Release] = {}
    for entry in catalog["dataset"]:
        if entry["title"].split(" : ")[0] != title:
            continue
        year = int(entry["temporal"][0]["startDate"][:4])
        csv_urls = [d["downloadURL"] for d in entry["distribution"] if d.get("format") == "CSV"]
        # The most recent year is listed twice as an API (once under its own ID, once as
        # "latest"); both point to the same data, so any of their dictionaries will do.
        dictionaries = [
            d["describedBy"]["downloadURL"]
            for d in entry["distribution"]
            if d.get("format") == "API" and "describedBy" in d
        ]
        if len(csv_urls) != 1 or not dictionaries:
            raise ValueError(f"Expected one CSV and a data dictionary for {title} {year}")
        releases[year] = Release(
            dataset=dataset,
            year=year,
            csv_url=csv_urls[0],
            dictionary_url=dictionaries[0],
            modified=entry.get("modified", ""),
        )
    return releases


def fetch_catalog(client: httpx.Client) -> dict:
    return get_json(client, CATALOG_URL)


def fetch_expected_columns(client: httpx.Client, release: Release) -> list[str]:
    """Column names from the CMS data dictionary for this release."""
    return [field["name"] for field in get_json(client, release.dictionary_url)["data"]["fields"]]


# --- One release, end to end ---------------------------------------------------------------


def release_dir(raw_dir: Path, release: Release) -> Path:
    """Where a release is stored. The `year=2024` folder name lets DuckDB read every year at once
    and get a `year` column for free (this is called Hive-style partitioning)."""
    return raw_dir / f"partd_{release.dataset}" / f"year={release.year}"


def is_up_to_date(out_dir: Path, release: Release) -> bool:
    """True if this exact release is already saved.

    CMS re-publishes past years from time to time (corrections, new methodology). A new
    publication gets a new file URL, so comparing the saved URL with the catalog's tells us
    whether the copy we have is still the current one.
    """
    source = out_dir / "_source.json"
    if not (out_dir / "data.parquet").exists() or not source.exists():
        return False
    saved = json.loads(source.read_text())
    if saved.get("csv_url") != release.csv_url:
        log.info(
            "%s %s was re-published by CMS (%s -> %s); downloading the new version",
            release.dataset,
            release.year,
            saved.get("modified", "unknown date"),
            release.modified or "unknown date",
        )
        return False
    return True


def select_years(available: Iterable[int], requested: list[int] | None) -> list[int]:
    """The years to download: those requested, or every year from FIRST_YEAR to the latest
    one CMS has published, so a newly released year is picked up without changing the code."""
    available = sorted(available)
    if requested is None:
        return [y for y in available if y >= FIRST_YEAR]
    missing = [y for y in requested if y not in available]
    if missing:
        raise ValueError(f"CMS has no release for {missing}; latest is {available[-1]}")
    return requested


def ingest_release(
    client: httpx.Client, release: Release, raw_dir: Path = RAW_DIR, force: bool = False
) -> int | None:
    """Download, check and convert one release. Returns the row count, or None if skipped."""
    out_dir = release_dir(raw_dir, release)
    parquet_path = out_dir / "data.parquet"
    if not force and is_up_to_date(out_dir, release):
        log.info("%s %s is up to date; skipping", release.dataset, release.year)
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "download.csv"
    # Fetch the small data dictionary first, so a failure here doesn't waste a 4 GB download.
    expected_columns = fetch_expected_columns(client, release)
    log.info("Downloading %s %s", release.dataset, release.year)
    download_with_retries(client, release.csv_url, csv_path)
    try:
        header = read_header(csv_path)
        check_columns(header, expected_columns, csv_path.name)
        log.info("Converting %s %s to Parquet", release.dataset, release.year)
        rows, repaired_lines = convert_csv(
            csv_path, parquet_path, label=f"{release.dataset} {release.year}"
        )
    finally:
        csv_path.unlink(missing_ok=True)

    metadata = {
        **asdict(release),
        "rows": rows,
        "repaired_lines": repaired_lines,
        "columns": header,
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "_source.json").write_text(json.dumps(metadata, indent=2) + "\n")
    log.info("%s %s: %s rows", release.dataset, release.year, f"{rows:,}")
    return rows


# --- Command line --------------------------------------------------------------------------


def parse_years(text: str) -> list[int]:
    """'2018-2024' -> [2018, ..., 2024]; '2019,2021' -> [2019, 2021]."""
    years: set[int] = set()
    for part in text.split(","):
        if "-" in part:
            start, end = (int(x) for x in part.split("-"))
            years.update(range(start, end + 1))
        else:
            years.add(int(part))
    return sorted(years)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dataset", choices=[*DATASET_TITLES, "all"], default="all", help="default: all"
    )
    parser.add_argument(
        "--years",
        type=parse_years,
        default=None,
        help=f"e.g. 2018-2024 or 2019,2021 (default: {FIRST_YEAR} to the latest published year)",
    )
    parser.add_argument(
        "--force", action="store_true", help="download again even if already up to date"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    datasets = list(DATASET_TITLES) if args.dataset == "all" else [args.dataset]

    with new_client() as client:
        catalog = fetch_catalog(client)
        for dataset in datasets:
            releases = find_releases(catalog, dataset)
            try:
                years = select_years(releases, args.years)
            except ValueError as error:
                log.error("%s: %s", dataset, error)
                return 1
            for year in years:
                ingest_release(client, releases[year], force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
