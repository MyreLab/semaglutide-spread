"""Download Scotland's diabetes prescribing and GP practice register from Public Health Scotland.

Public Health Scotland's open-data portal (opendata.nhs.scot) publishes "Prescriptions in the
Community" each month, by prescribing GP practice, and a quarterly register of GP practices
with their address, postcode and number of registered patients. Like England, the portal can
run SQL on its side, so only the diabetes rows are downloaded, already added up.

For each month ("Data by Prescriber Location"), two queries:
1. Diabetes drugs: one row per practice x BNF chemical substance (the first 9 characters of
   the BNF code), for BNF 6.1.1 and 6.1.2, with semaglutide rows also split by product name.
2. Practices: every practice code with any prescribing that month, with its total items.
Months are by *paid date*: when the dispensing was paid for, usually the month after it was
dispensed.

The practice register is saved whole for each quarter (about 900 practices).

Each query is fetched in pages of up to 32,000 rows in a fixed order, and the pages must add
up to the query's own total.

Outputs:
    data/raw/phs_diabetes/year_month=YYYY-MM/data.parquet (+ _source.json)
    data/raw/phs_practices/year_month=YYYY-MM/data.parquet
    data/raw/phs_register/quarter=YYYY-MM/data.parquet (+ _source.json)

Usage:
    uv run phs-download
    uv run phs-download --months 2019-01:2019-12
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import logging
import re
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
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
from semaglutide_spread.sources.epd import DIABETES_SECTIONS, FIRST_MONTH, TARGET_CHEMICAL

log = logging.getLogger(__name__)

API = "https://www.opendata.nhs.scot/api/3/action"
PRESCRIBING_PACKAGE = "prescriptions-in-the-community"
REGISTER_PACKAGE = "gp-practice-contact-details-and-list-sizes"
PAGE_SIZE = 32_000
MONTHS = {name: number for number, name in enumerate(calendar.month_name) if name}

DIABETES_COLUMNS = ("practice_code", "bnf_chemical_code", "target_presentation", "items",
                    "paid_quantity", "gross_ingredient_cost")  # fmt: skip
PRACTICE_COLUMNS = ("practice_code", "items")
# The register's column names changed over the years: this project's name -> the names used.
REGISTER_ALIASES = {
    "practice_code": ("PracticeCode",),
    "practice_name": ("GPPracticeName", "PracticeName"),
    "list_size": ("PracticeListSize", "Listsize"),
    "address_1": ("AddressLine1",),
    "address_2": ("AddressLine2",),
    "address_3": ("AddressLine3",),
    "address_4": ("AddressLine4",),
    "postcode": ("Postcode",),
    "practice_type": ("PracticeType",),
    "health_board": ("HB",),
}
REGISTER_COLUMNS = tuple(REGISTER_ALIASES)


class TotalsMismatchError(Exception):
    """The pages of a query don't add up to the query's own total."""


@dataclass(frozen=True)
class Resource:
    month: str  # YYYY-MM
    resource_id: str
    name: str
    last_modified: str | None
    size: int | None

    @property
    def version(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "last_modified": self.last_modified,
            "size": self.size,
        }


def month_from_name(name: str) -> str | None:
    """'Data by Prescriber Location - July 2026' -> '2026-07' (any dash)."""
    match = re.search(r"(" + "|".join(MONTHS) + r")\s+(\d{4})\s*$", name)
    return f"{match[2]}-{MONTHS[match[1]]:02d}" if match else None


def find_resources(client: httpx.Client, package: str, title: str) -> dict[str, Resource]:
    """Resources of `package` whose name starts with `title`, by month."""
    url = f"{API}/package_show?" + urlencode({"id": package})
    body = with_retries(lambda: _get_json(client, url), f"catalog {package}")
    found = {}
    for r in body["result"]["resources"]:
        month = month_from_name(r["name"])
        if r["name"].startswith(title) and month:
            found[month] = Resource(
                month, r["id"], r["name"], r.get("last_modified"), r.get("size")
            )
    return dict(sorted(found.items()))


def _get_json(client: httpx.Client, url: str) -> dict:
    response = client.get(url)
    response.raise_for_status()
    return response.json()


def run_sql(client: httpx.Client, sql: str) -> list[dict]:
    url = f"{API}/datastore_search_sql?" + urlencode({"sql": " ".join(sql.split())})
    body = with_retries(lambda: _get_json(client, url), "PHS query")
    if not body.get("success"):
        raise RuntimeError(f"PHS query failed: {body.get('error')}")
    if body["result"].get("records_truncated") in (True, "true"):
        raise RuntimeError("PHS result truncated; lower PAGE_SIZE")
    return body["result"]["records"]


def fetch_pages(client: httpx.Client, select: str, order_by: str, page_size: int) -> Iterator[dict]:
    offset = 0
    while True:
        page = run_sql(client, f"{select} ORDER BY {order_by} LIMIT {page_size} OFFSET {offset}")
        yield from page
        if len(page) < page_size:
            return
        offset += page_size


def diabetes_query(resource_id: str) -> tuple[str, str]:
    sections = ", ".join(f"'{s}'" for s in DIABETES_SECTIONS)
    select = f"""
        SELECT
            "GPPractice" AS practice_code,
            LEFT("BNFItemCode", 9) AS bnf_chemical_code,
            CASE WHEN LEFT("BNFItemCode", 9) = '{TARGET_CHEMICAL}' THEN "BNFItemDescription" END
                AS target_presentation,
            SUM("NumberOfPaidItems") AS items,
            SUM("PaidQuantity") AS paid_quantity,
            SUM("GrossIngredientCost") AS gross_ingredient_cost
        FROM "{resource_id}"
        WHERE LEFT("BNFItemCode", 6) IN ({sections})
        GROUP BY 1, 2, 3"""
    return select, "1, 2, 3"


def practices_query(resource_id: str) -> tuple[str, str]:
    select = f"""
        SELECT "GPPractice" AS practice_code, SUM("NumberOfPaidItems") AS items
        FROM "{resource_id}"
        GROUP BY 1"""
    return select, "1"


def save_rows(rows: list[dict], columns: tuple[str, ...], parquet_path: Path) -> int:
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


def is_up_to_date(source: Path, resource: Resource, *others: Path) -> bool:
    if not source.exists() or not all(p.exists() for p in others):
        return False
    return json.loads(source.read_text()).get("version") == resource.version


def ingest_month(
    client: httpx.Client,
    resource: Resource,
    raw_dir: Path = RAW_DIR,
    force: bool = False,
    page_size: int = PAGE_SIZE,
) -> dict | None:
    folder = f"year_month={resource.month}"
    source = raw_dir / "phs_diabetes" / folder / "_source.json"
    if not force and is_up_to_date(
        source, resource, raw_dir / "phs_practices" / folder / "data.parquet"
    ):
        log.info("%s is up to date; skipping", resource.month)
        return None

    summary: dict = {
        "month": resource.month,
        "resource_name": resource.name,
        "version": resource.version,
    }
    for name, query, columns in (
        ("practices", practices_query, PRACTICE_COLUMNS),
        ("diabetes", diabetes_query, DIABETES_COLUMNS),
    ):
        select, order_by = query(resource.resource_id)
        ((total,),) = [
            tuple(r.values())
            for r in run_sql(client, f"SELECT SUM(items) AS items FROM ({select}) AS q")
        ]
        expected = int(float(total or 0))
        rows = list(fetch_pages(client, select, order_by, page_size))
        found = sum(int(float(r["items"] or 0)) for r in rows)
        if found != expected:
            raise TotalsMismatchError(
                f"{resource.month} {name}: pages add up to {found:,} items; expected {expected:,}"
            )
        out = raw_dir / f"phs_{name}" / folder / "data.parquet"
        summary[name] = {"rows": save_rows(rows, columns, out), "items": expected, "sql": select}

    summary["downloaded_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    source.write_text(json.dumps(summary, indent=2) + "\n")
    log.info("%s: %s practice codes, %s diabetes rows", resource.month,
             f"{summary['practices']['rows']:,}", f"{summary['diabetes']['rows']:,}")  # fmt: skip
    return summary


def ingest_register(
    client: httpx.Client, resource: Resource, raw_dir: Path = RAW_DIR, force: bool = False
) -> int | None:
    """Save one quarter's GP practice register."""
    folder = raw_dir / "phs_register" / f"quarter={resource.month}"
    source = folder / "_source.json"
    if not force and is_up_to_date(source, resource, folder / "data.parquet"):
        return None
    url = f"{API}/datastore_search?" + urlencode(
        {"resource_id": resource.resource_id, "limit": PAGE_SIZE}
    )
    body = with_retries(lambda: _get_json(client, url), f"register {resource.month}")
    records = body["result"]["records"]
    if body["result"]["total"] > len(records):
        raise RuntimeError(f"register {resource.month}: more than {PAGE_SIZE} rows")
    rows = [normalise_register_row(r, resource.name) for r in records]
    written = save_rows(rows, REGISTER_COLUMNS, folder / "data.parquet")
    source.write_text(json.dumps({
        "quarter": resource.month, "resource_name": resource.name, "version": resource.version,
        "rows": written, "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }, indent=2) + "\n")  # fmt: skip
    log.info("register %s: %s practices", resource.month, f"{written:,}")
    return written


def normalise_register_row(record: dict, label: str) -> dict:
    """Rename one register row's columns to this project's names, whichever names it uses."""
    row = {}
    for column, aliases in REGISTER_ALIASES.items():
        name = next((a for a in aliases if a in record), None)
        if name is None:
            raise SchemaMismatchError(f"{label}: no column for {column} (tried {aliases})")
        row[column] = record[name]
    return row


def select_months(available: list[str], wanted: str | None = None) -> list[str]:
    first, last = wanted.split(":") if wanted else (FIRST_MONTH, available[-1])
    return [m for m in available if first <= m <= last]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--months", help='range to fetch, e.g. "2019-01:2019-12"')
    parser.add_argument("--force", action="store_true", help="download again even if saved")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    with new_client() as client:
        registers = find_resources(client, REGISTER_PACKAGE, "GP Practices and List sizes")
        for quarter in select_months(list(registers), args.months):
            ingest_register(client, registers[quarter], force=args.force)
        months = find_resources(client, PRESCRIBING_PACKAGE, "Data by Prescriber Location")
        for month in select_months(list(months), args.months):
            ingest_month(client, months[month], force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
