"""Download Census Bureau reference files used to place prescribers on the map.

CMS gives each prescriber's 5-digit ZIP code. ZIP codes are mail routes, not areas, so the
Census Bureau publishes ZIP Code Tabulation Areas (ZCTAs): the closest area-based equivalent.
Two 2020 files are used:

- ZCTA gazetteer: one row per ZCTA, with its centre point (latitude/longitude)
- ZCTA-to-county relationship file: which counties each ZCTA overlaps, and by how much land

They are stored, like every source, as text-only Parquet under data/raw/census_<name>/.

Usage:
    uv run census-download
    uv run census-download --force
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from semaglutide_spread.paths import RAW_DIR
from semaglutide_spread.sources.common import (
    check_columns,
    convert_csv,
    download_with_retries,
    new_client,
    read_header,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReferenceFile:
    name: str
    url: str
    delimiter: str
    expected_columns: tuple[str, ...]


REFERENCE_FILES = (
    ReferenceFile(
        name="zcta_gazetteer_2020",
        url=(
            "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
            "2020_Gazetteer/2020_Gaz_zcta_national.zip"
        ),
        delimiter="\t",
        expected_columns=("GEOID", "ALAND", "INTPTLAT", "INTPTLONG"),
    ),
    ReferenceFile(
        name="zcta_county_2020",
        url=(
            "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
            "tab20_zcta520_county20_natl.txt"
        ),
        delimiter="|",
        expected_columns=(
            "GEOID_ZCTA5_20",
            "GEOID_COUNTY_20",
            "NAMELSAD_COUNTY_20",
            "AREALAND_PART",
        ),
    ),
)


def extract_single_file(zip_path: Path, dest: Path) -> None:
    """Unzip an archive that contains exactly one file, saving it as `dest`."""
    with zipfile.ZipFile(zip_path) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"{zip_path.name} should contain one file, found {names}")
        with archive.open(names[0]) as src, dest.open("wb") as dst:
            while chunk := src.read(8 * 1024 * 1024):
                dst.write(chunk)


def ingest_reference_file(
    client: httpx.Client, ref: ReferenceFile, raw_dir: Path = RAW_DIR, force: bool = False
) -> int | None:
    """Download, check and convert one reference file. Returns the row count, or None if
    the same file (same URL) is already saved."""
    out_dir = raw_dir / f"census_{ref.name}"
    parquet_path = out_dir / "data.parquet"
    source = out_dir / "_source.json"
    if (
        not force
        and parquet_path.exists()
        and source.exists()
        and json.loads(source.read_text()).get("url") == ref.url
    ):
        log.info("%s is up to date; skipping", ref.name)
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    download_path = out_dir / ("download.zip" if ref.url.endswith(".zip") else "download.txt")
    text_path = out_dir / "download.txt"
    log.info("Downloading %s", ref.name)
    download_with_retries(client, ref.url, download_path)
    try:
        if download_path.suffix == ".zip":
            extract_single_file(download_path, text_path)
        header = read_header(text_path, delimiter=ref.delimiter)
        check_columns(header, ref.expected_columns, ref.name, warn_extra=False)
        rows, repaired_lines = convert_csv(
            text_path, parquet_path, label=ref.name, delimiter=ref.delimiter
        )
    finally:
        download_path.unlink(missing_ok=True)
        text_path.unlink(missing_ok=True)

    metadata = {
        **asdict(ref),
        "rows": rows,
        "repaired_lines": repaired_lines,
        "columns": header,
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    source.write_text(json.dumps(metadata, indent=2) + "\n")
    log.info("%s: %s rows", ref.name, f"{rows:,}")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--force", action="store_true", help="download again even if saved")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with new_client() as client:
        for ref in REFERENCE_FILES:
            ingest_reference_file(client, ref, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
