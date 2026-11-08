"""Download Medicare Part D prescriber files from data.cms.gov and store them as Parquet.

Two CMS datasets are used, one release per data year:

- "by Provider and Drug": one row per prescriber x drug (the adoption data)
- "by Provider": one row per prescriber, including ZIP code (the location data)

For each year the CSV is downloaded, checked against the CMS data dictionary, converted to
Parquet with every column kept as text, and the CSV is deleted. Types are assigned later, in
the dbt staging models, so nothing is lost or misread at this stage (for example, the
leading zero in state FIPS code "06").

Usage:
    uv run partd-download --years 2018-2024
    uv run partd-download --dataset by_provider --years 2024 --force
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx

from semaglutide_spread.paths import RAW_DIR

log = logging.getLogger(__name__)

CATALOG_URL = "https://data.cms.gov/data.json"

DATASET_TITLES = {
    "by_provider_and_drug": "Medicare Part D Prescribers - by Provider and Drug",
    "by_provider": "Medicare Part D Prescribers - by Provider",
}

DEFAULT_YEARS = range(2018, 2025)  # semaglutide (Ozempic) was approved in December 2017

TIMEOUT = httpx.Timeout(60.0, read=300.0)
# Identify the project to the servers we download from (some block anonymous clients).
HEADERS = {"User-Agent": "semaglutide-spread (+https://github.com/MyreLab/semaglutide-spread)"}
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB


class SchemaMismatchError(Exception):
    """A downloaded file is missing columns that the CMS data dictionary says it should have."""


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
    response = client.get(CATALOG_URL)
    response.raise_for_status()
    return response.json()


def fetch_expected_columns(client: httpx.Client, release: Release) -> list[str]:
    """Column names from the CMS data dictionary for this release."""
    response = client.get(release.dictionary_url)
    response.raise_for_status()
    return [field["name"] for field in response.json()["data"]["fields"]]


# --- Downloading ---------------------------------------------------------------------------


def download_file(client: httpx.Client, url: str, dest: Path) -> None:
    """Stream `url` to `dest`, resuming a partial download if one exists.

    Data is written to `dest` + ".part" and renamed only when complete, so a file at `dest`
    is never a half-finished download.
    """
    part = dest.with_name(dest.name + ".part")
    already = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={already}-"} if already else {}

    with client.stream("GET", url, headers=headers) as response:
        if response.status_code == 416:  # nothing left to fetch: the .part file is complete
            part.rename(dest)
            return
        response.raise_for_status()
        resuming = response.status_code == 206
        if already and not resuming:
            log.info("Server ignored the resume request; starting %s again", dest.name)
        with part.open("ab" if resuming else "wb") as f:
            for chunk in response.iter_bytes(CHUNK_SIZE):
                f.write(chunk)
    part.rename(dest)


# --- Converting ----------------------------------------------------------------------------


def read_header(csv_path: Path) -> list[str]:
    # Read only the first line as bytes, so a broken byte further down can't trip this up.
    with csv_path.open("rb") as f:
        first_line = f.readline().decode("utf-8-sig")
    return next(csv.reader([first_line]))


def check_columns(found: list[str], expected: Iterable[str], source: str) -> None:
    missing = [c for c in expected if c not in found]
    if missing:
        raise SchemaMismatchError(f"{source} is missing columns: {', '.join(missing)}")
    extra = [c for c in found if c not in set(expected)]
    if extra:
        log.warning("%s has columns not in the data dictionary: %s", source, ", ".join(extra))


def csv_to_parquet(csv_path: Path, parquet_path: Path) -> int:
    """Convert a CSV to a zstd-compressed Parquet file, keeping every column as text.

    Returns the number of rows written.
    """
    tmp = parquet_path.with_name(parquet_path.name + ".tmp")
    con = duckdb.connect()
    try:
        con.execute("SET preserve_insertion_order = false")  # lets DuckDB stream with less memory
        con.execute(
            f"""
            COPY (
                SELECT * FROM read_csv({_sql_str(csv_path)}, header = true, all_varchar = true)
            ) TO {_sql_str(tmp)} (FORMAT parquet, COMPRESSION zstd)
            """
        )
        (rows,) = con.execute(f"SELECT count(*) FROM read_parquet({_sql_str(tmp)})").fetchone()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        con.close()
    tmp.rename(parquet_path)
    return rows


def repair_utf8(csv_path: Path) -> int:
    """Replace bytes that aren't valid UTF-8 with "�" (U+FFFD), line by line, in place.

    The CMS files are UTF-8 but can contain the odd corrupted line: the 2020 "by Provider and
    Drug" file has one city name with broken bytes. Rather than dropping that row, the bad
    bytes become "�", the same character CMS itself already uses elsewhere in its files.
    Returns the number of lines changed.
    """
    fixed = csv_path.with_name(csv_path.name + ".utf8")
    repaired = 0
    with csv_path.open("rb") as src, fixed.open("wb") as dst:
        for line in src:
            if not line.isascii():
                try:
                    line.decode("utf-8")
                except UnicodeDecodeError:
                    line = line.decode("utf-8", errors="replace").encode("utf-8")
                    repaired += 1
            dst.write(line)
    fixed.replace(csv_path)
    return repaired


def _sql_str(path: Path) -> str:
    """Quote a file path as a SQL string literal."""
    return "'" + str(path).replace("'", "''") + "'"


# --- One release, end to end ---------------------------------------------------------------


def release_dir(raw_dir: Path, release: Release) -> Path:
    """Where a release is stored. The `year=2024` folder name lets DuckDB read every year at once
    and get a `year` column for free (this is called Hive-style partitioning)."""
    return raw_dir / f"partd_{release.dataset}" / f"year={release.year}"


def ingest_release(
    client: httpx.Client, release: Release, raw_dir: Path = RAW_DIR, force: bool = False
) -> int | None:
    """Download, check and convert one release. Returns the row count, or None if skipped."""
    out_dir = release_dir(raw_dir, release)
    parquet_path = out_dir / "data.parquet"
    if parquet_path.exists() and not force:
        log.info("%s %s already saved; skipping", release.dataset, release.year)
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "download.csv"
    log.info("Downloading %s %s", release.dataset, release.year)
    download_file(client, release.csv_url, csv_path)
    try:
        header = read_header(csv_path)
        check_columns(header, fetch_expected_columns(client, release), csv_path.name)
        log.info("Converting %s %s to Parquet", release.dataset, release.year)
        repaired_lines = 0
        try:
            rows = csv_to_parquet(csv_path, parquet_path)
        except duckdb.InvalidInputException as error:
            if "unicode" not in str(error).lower():
                raise
            repaired_lines = repair_utf8(csv_path)
            log.warning(
                "%s %s: replaced invalid UTF-8 bytes on %d line(s)",
                release.dataset,
                release.year,
                repaired_lines,
            )
            rows = csv_to_parquet(csv_path, parquet_path)
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
        default=list(DEFAULT_YEARS),
        help="e.g. 2018-2024 or 2019,2021 (default: 2018-2024)",
    )
    parser.add_argument("--force", action="store_true", help="download again even if saved")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    datasets = list(DATASET_TITLES) if args.dataset == "all" else [args.dataset]

    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS) as client:
        catalog = fetch_catalog(client)
        for dataset in datasets:
            releases = find_releases(catalog, dataset)
            missing = [y for y in args.years if y not in releases]
            if missing:
                log.error("%s has no release for: %s", dataset, missing)
                return 1
            for year in args.years:
                ingest_release(client, releases[year], force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
