"""Download England's diabetes prescribing, by GP practice and month, from the NHSBSA.

The English Prescribing Dataset (EPD) lists every prescription item dispensed in the community
in England: ~17 million rows a month, ~7 GB of CSV each. This project needs only diabetes drugs,
so instead of downloading whole files it asks the NHSBSA's open-data API to run a SQL query on
its side and send back just the diabetes rows, already added up by practice and drug.

Two NHSBSA datasets cover the period:
- "English Prescribing Dataset (EPD) with SNOMED code": November 2020 onwards, still updated;
- "English Prescribing Dataset (EPD)" (retired): January 2014 to June 2025.
Each month is taken from the SNOMED dataset when it has that month, otherwise from the retired
one. Column names changed in mid-2025 (BNF_CHEMICAL_SUBSTANCE went from holding the code to
holding the name), so the downloader looks at each month's columns first, picks the matching
layout, and each query renames the columns to one shared set. Column types also vary (in some
months the costs are stored as text), so every number is cast before it is added up.

For each month, two queries:
1. Diabetes drugs: one row per practice x BNF chemical substance, for BNF sections 6.1.1
   (insulins) and 6.1.2 (antidiabetic drugs), the BNF equivalent of WHO ATC A10. Semaglutide
   rows are also split by presentation (e.g. "Ozempic 1mg..."), to tell the brands apart.
2. Practices: every practice that prescribed anything that month, with its address and
   postcode (the all-practices denominator, and how practices get on the map). NHS areas are
   left out: they were renamed and redrawn over the years (STPs, then ICBs), so areas are
   assigned later from the postcode, the same way for every month.

The API returns at most 32,000 rows per request, so results are fetched in pages, in a fixed
order. A third, one-row query asks for the month's total items, which must equal the sum of
the pages: a check that no page was skipped or repeated.

Steps:
1. Read both dataset catalogs (CKAN `package_show`) and list the months each one has.
2. Pick the months to fetch: by default January 2018 to the latest published.
3. For each month: skip it if it is already saved from the same resource (NHSBSA replaces the
   resource when it re-publishes a month); otherwise run the queries, page by page, check the
   totals, save each result as text-only Parquet, and write `_source.json`.

Outputs (one folder per month):
    data/raw/epd_diabetes/year_month=YYYY-MM/data.parquet
    data/raw/epd_practices/year_month=YYYY-MM/data.parquet
    data/raw/epd_diabetes/year_month=YYYY-MM/_source.json

Usage:
    uv run epd-download
    uv run epd-download --months 2019-01:2019-12
    uv run epd-download --force
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx

from semaglutide_spread.paths import RAW_DIR
from semaglutide_spread.sources.common import (
    SchemaMismatchError,
    convert_csv,
    new_client,
    with_retries,
)

log = logging.getLogger(__name__)

API = "https://opendata.nhsbsa.net/api/3/action"
FIRST_MONTH = "2018-01"  # the same start as the US data
PAGE_SIZE = 32_000  # the most rows the API returns in one response
TARGET_CHEMICAL = "0601023AW"  # semaglutide's BNF chemical substance code
DIABETES_SECTIONS = ("060101", "060102")  # BNF 6.1.1 insulins, 6.1.2 antidiabetic drugs


class TotalsMismatchError(Exception):
    """The pages of a query don't add up to the query's own total."""


@dataclass(frozen=True)
class Dataset:
    """One NHSBSA dataset: a CKAN package with one resource per month."""

    package: str
    resource_prefix: str  # resource names are prefix + YYYYMM


SNOMED = Dataset("english-prescribing-dataset-epd-with-snomed-code", "EPD_SNOMED_")
RETIRED = Dataset("english-prescribing-data-epd", "EPD_")
DATASETS = (SNOMED, RETIRED)  # in order of preference


@dataclass(frozen=True)
class Layout:
    """What a month's file calls the columns this project needs."""

    name: str
    chemical_code: str
    chemical_name: str
    presentation_name: str


# Until mid-2025: BNF_CHEMICAL_SUBSTANCE is the code, CHEMICAL_SUBSTANCE_BNF_DESCR the name.
ORIGINAL = Layout(
    "original", "BNF_CHEMICAL_SUBSTANCE", "CHEMICAL_SUBSTANCE_BNF_DESCR", "BNF_DESCRIPTION"
)
# From mid-2025: BNF_CHEMICAL_SUBSTANCE_CODE is the code, BNF_CHEMICAL_SUBSTANCE the name.
RENAMED = Layout(
    "renamed", "BNF_CHEMICAL_SUBSTANCE_CODE", "BNF_CHEMICAL_SUBSTANCE", "BNF_PRESENTATION_NAME"
)
LAYOUTS = (RENAMED, ORIGINAL)
SHARED_COLUMNS = (
    "PRACTICE_CODE", "PRACTICE_NAME", "ADDRESS_1", "ADDRESS_2", "ADDRESS_3", "ADDRESS_4",
    "POSTCODE", "ITEMS", "TOTAL_QUANTITY", "NIC", "ACTUAL_COST",
)  # fmt: skip

DIABETES_COLUMNS = (
    "practice_code",
    "bnf_chemical_code",
    "bnf_chemical_name",
    "target_presentation",
    "items",
    "total_quantity",
    "nic",
    "actual_cost",
)
PRACTICE_COLUMNS = (
    "practice_code",
    "practice_name",
    "address_1",
    "address_2",
    "address_3",
    "address_4",
    "postcode",
    "items",
)


@dataclass(frozen=True)
class Release:
    """One month of one dataset, as listed in its catalog."""

    month: str  # YYYY-MM
    dataset: Dataset
    resource_id: str
    resource_name: str
    size: int | None
    created: str | None
    layout: Layout | None = None  # filled in by detect_layout()

    @property
    def version(self) -> dict:
        """What identifies this exact file: a re-published month gets a new resource."""
        return {"resource_id": self.resource_id, "size": self.size, "created": self.created}


# --- Catalog -------------------------------------------------------------------------------


def find_releases(client: httpx.Client) -> dict[str, Release]:
    """Every month either dataset has, mapped to the preferred dataset's release."""
    releases: dict[str, Release] = {}
    for dataset in reversed(DATASETS):  # least preferred first, so preferred ones overwrite
        url = f"{API}/package_show?" + urlencode({"id": dataset.package})
        package = with_retries(lambda u=url: _get_json(client, u), f"catalog {dataset.package}")
        for resource in package["result"]["resources"]:
            name = resource["name"]
            suffix = name.removeprefix(dataset.resource_prefix)
            if not (name.startswith(dataset.resource_prefix) and suffix.isdigit()):
                continue
            month = f"{suffix[:4]}-{suffix[4:]}"
            releases[month] = Release(
                month, dataset, resource["id"], name, resource.get("size"), resource.get("created")
            )
    return dict(sorted(releases.items()))


def select_months(available: list[str], wanted: str | None = None) -> list[str]:
    """Months to fetch: `wanted` as "YYYY-MM:YYYY-MM", or FIRST_MONTH to the latest."""
    first, last = wanted.split(":") if wanted else (FIRST_MONTH, available[-1])
    return [m for m in available if first <= m <= last]


# --- Queries -------------------------------------------------------------------------------


def detect_layout(client: httpx.Client, release: Release) -> Release:
    """Look at one row of the month to find which column names it uses."""
    (row,) = run_sql(client, release, f"SELECT * FROM `{release.resource_name}` LIMIT 1")
    columns = set(row)
    for layout in LAYOUTS:
        needed = {layout.chemical_code, layout.chemical_name, layout.presentation_name}
        if needed | set(SHARED_COLUMNS) <= columns:
            return replace(release, layout=layout)
    raise SchemaMismatchError(f"{release.resource_name}: unknown columns {sorted(columns)}")


def diabetes_query(release: Release) -> tuple[str, str]:
    """(select, order by) for the month's diabetes rows, in this project's column names."""
    d = release.layout
    assert d is not None, "call detect_layout() first"
    sections = ", ".join(f"'{s}'" for s in DIABETES_SECTIONS)
    select = f"""
        SELECT
            PRACTICE_CODE AS practice_code,
            {d.chemical_code} AS bnf_chemical_code,
            {d.chemical_name} AS bnf_chemical_name,
            CASE WHEN {d.chemical_code} = '{TARGET_CHEMICAL}' THEN {d.presentation_name} END
                AS target_presentation,
            SUM(CAST(ITEMS AS INT64)) AS items,
            SUM(CAST(TOTAL_QUANTITY AS FLOAT64)) AS total_quantity,
            SUM(CAST(NIC AS FLOAT64)) AS nic,
            SUM(CAST(ACTUAL_COST AS FLOAT64)) AS actual_cost
        FROM `{release.resource_name}`
        WHERE SUBSTR({d.chemical_code}, 1, 6) IN ({sections})
        GROUP BY 1, 2, 3, 4"""
    return select, "practice_code, bnf_chemical_code, target_presentation"


def practices_query(release: Release) -> tuple[str, str]:
    select = f"""
        SELECT
            PRACTICE_CODE AS practice_code,
            ANY_VALUE(PRACTICE_NAME) AS practice_name,
            ANY_VALUE(ADDRESS_1) AS address_1,
            ANY_VALUE(ADDRESS_2) AS address_2,
            ANY_VALUE(ADDRESS_3) AS address_3,
            ANY_VALUE(ADDRESS_4) AS address_4,
            ANY_VALUE(POSTCODE) AS postcode,
            SUM(CAST(ITEMS AS INT64)) AS items
        FROM `{release.resource_name}`
        GROUP BY 1"""
    return select, "practice_code"


def _get_json(client: httpx.Client, url: str) -> dict:
    response = client.get(url)
    response.raise_for_status()
    return response.json()


def run_sql(client: httpx.Client, release: Release, sql: str) -> list[dict]:
    """Run one query; return its records. Fails if the API cut the result short."""
    url = f"{API}/datastore_search_sql?" + urlencode(
        {"resource_id": release.resource_name, "sql": " ".join(sql.split())}
    )
    body = with_retries(lambda: _get_json(client, url), f"query on {release.resource_name}")
    if not body.get("success"):
        raise RuntimeError(f"{release.resource_name}: query failed: {body.get('error')}")
    result = body["result"]["result"]
    if result.get("records_truncated") in (True, "true"):
        raise RuntimeError(f"{release.resource_name}: result truncated; lower PAGE_SIZE")
    return result["records"]


def fetch_pages(
    client: httpx.Client, release: Release, select: str, order_by: str, page_size: int
) -> Iterator[dict]:
    """Yield every row of `select`, one page at a time, in a fixed order (so pages don't
    overlap or skip rows)."""
    offset = 0
    while True:
        page = run_sql(
            client, release, f"{select} ORDER BY {order_by} LIMIT {page_size} OFFSET {offset}"
        )
        yield from page
        if len(page) < page_size:
            return
        offset += page_size


def total_items(client: httpx.Client, release: Release, select: str) -> int:
    """The query's total items, computed by the API in one row (to check the pages)."""
    (row,) = run_sql(client, release, f"SELECT SUM(items) AS items FROM ({select})")
    return int(row["items"] or 0)


# --- Saving --------------------------------------------------------------------------------


def save_rows(rows: list[dict], columns: tuple[str, ...], parquet_path: Path) -> int:
    """Write rows as text-only Parquet, like every raw file in this project."""
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "rows.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(["" if r.get(c) is None else r[c] for c in columns] for r in rows)
        rows_written, _ = convert_csv(
            csv_path, parquet_path, label=parquet_path.parent.name, delimiter=","
        )
    return rows_written


def is_up_to_date(release: Release, raw_dir: Path) -> bool:
    source = raw_dir / "epd_diabetes" / f"year_month={release.month}" / "_source.json"
    practices = raw_dir / "epd_practices" / f"year_month={release.month}" / "data.parquet"
    if not (source.exists() and practices.exists()):
        return False
    return json.loads(source.read_text()).get("version") == release.version


def ingest_month(
    client: httpx.Client,
    release: Release,
    raw_dir: Path = RAW_DIR,
    force: bool = False,
    page_size: int = PAGE_SIZE,
) -> dict | None:
    """Fetch, check and save one month. Returns its summary, or None if already saved."""
    if not force and is_up_to_date(release, raw_dir):
        log.info("%s is up to date; skipping", release.month)
        return None

    release = detect_layout(client, release)
    summary: dict = {
        "month": release.month,
        "dataset": release.dataset.package,
        "resource_name": release.resource_name,
        "layout": release.layout.name,
        "version": release.version,
    }
    for name, query, columns in (
        ("practices", practices_query, PRACTICE_COLUMNS),
        ("diabetes", diabetes_query, DIABETES_COLUMNS),
    ):
        select, order_by = query(release)
        expected = total_items(client, release, select)
        rows = list(fetch_pages(client, release, select, order_by, page_size))
        found = sum(int(r["items"] or 0) for r in rows)
        if found != expected:
            raise TotalsMismatchError(
                f"{release.month} {name}: pages add up to {found:,} items; expected {expected:,}"
            )
        out = raw_dir / f"epd_{name}" / f"year_month={release.month}" / "data.parquet"
        summary[name] = {"rows": save_rows(rows, columns, out), "items": expected, "sql": select}

    summary["downloaded_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    source = raw_dir / "epd_diabetes" / f"year_month={release.month}" / "_source.json"
    source.write_text(json.dumps(summary, indent=2) + "\n")
    log.info(
        "%s: %s practices, %s diabetes rows (%s)",
        release.month,
        f"{summary['practices']['rows']:,}",
        f"{summary['diabetes']['rows']:,}",
        release.resource_name,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--months", help='range to fetch, e.g. "2019-01:2019-12"')
    parser.add_argument("--force", action="store_true", help="download again even if saved")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # hundreds of queries: log months only
    with new_client() as client:
        releases = find_releases(client)
        months = select_months(list(releases), args.months)
        log.info("%d months to check: %s to %s", len(months), months[0], months[-1])
        for month in months:
            ingest_month(client, releases[month], force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
