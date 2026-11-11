"""Download Northern Ireland's diabetes prescribing and GP practice reference files.

The Business Services Organisation publishes "GP Prescribing Data" on OpenDataNI each month:
one CSV per month (~60 MB), with every product prescribed by every GP practice. There is no
reliable query service for recent months, so each file is downloaded, filtered on this
computer with DuckDB, and deleted. Two results are kept per month:

1. Diabetes drugs: one row per practice x substance (VTM, "virtual therapeutic moiety", e.g.
   "Metformin"), for BNF chapter 6, section 1, paragraphs 1 and 2 (insulins and antidiabetic
   drugs), with semaglutide rows split by product name (e.g. "Ozempic 1mg..."). Northern
   Ireland's BNF codes don't follow the standard 15-character format, so the BNF chapter,
   section and paragraph columns are used instead.
2. Practices: every practice's total items that month.

The practice reference files (one or two a year) give each practice's address, postcode,
local commissioning group and registered patients.

The files are Latin-1 text (the "£" in the cost column names), not UTF-8, and column names
vary slightly between years, so columns are matched by name, ignoring case and spacing.

Outputs:
    data/raw/ni_diabetes/year_month=YYYY-MM/data.parquet (+ _source.json)
    data/raw/ni_practices/year_month=YYYY-MM/data.parquet
    data/raw/ni_register/year_month=YYYY-MM/data.parquet (+ _source.json)

Usage:
    uv run ni-download
    uv run ni-download --months 2019-01:2019-12
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import duckdb
import httpx

from semaglutide_spread.paths import RAW_DIR
from semaglutide_spread.sources.common import (
    SchemaMismatchError,
    download_with_retries,
    new_client,
    sql_str,
    with_retries,
)
from semaglutide_spread.sources.epd import FIRST_MONTH
from semaglutide_spread.sources.phs import month_from_name

log = logging.getLogger(__name__)

API = "https://admin.opendatani.gov.uk/api/3/action"
PRESCRIBING_PACKAGE = "gp-prescribing-data"
REGISTER_PACKAGE = "gp-practice-list-sizes"
TARGET_SUBSTANCE = "semaglutide"

# This project's column name -> a pattern matching the file's column name (lower case, spaces
# and punctuation removed).
PRESCRIBING_COLUMNS = {
    "practice": r"practice",
    "substance": r"vtmnm",
    "product": r"ampnm",  # the branded product, or "-" if prescribed by generic name
    "generic_product": r"vmpnm",
    "items": r"totalitems",
    "quantity": r"totalquantity",
    "gross_cost": r"grosscost.*",
    "actual_cost": r"actualcost.*",
    "bnf_chapter": r"bnfchapter",
    "bnf_section": r"bnfsection",
    "bnf_paragraph": r"bnfparagraph",
}
REGISTER_COLUMNS = {
    "practice_code": r"pracno|practiceno|practicenumber",
    "practice_name": r"practicename",
    "address_1": r"address1",
    "address_2": r"address2",
    "address_3": r"address3",
    "postcode": r"postcode",
    "lcg": r"lcg",
    "registered_patients": r"registeredpatients|listsize",
}


@dataclass(frozen=True)
class Resource:
    month: str
    resource_id: str
    name: str
    url: str
    last_modified: str | None
    size: int | None

    @property
    def version(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "last_modified": self.last_modified,
            "size": self.size,
        }


def find_resources(client: httpx.Client, package: str) -> dict[str, Resource]:
    url = f"{API}/package_show?" + urlencode({"id": package})

    def get():
        response = client.get(url)
        response.raise_for_status()
        return response.json()

    found = {}
    for r in with_retries(get, f"catalog {package}")["result"]["resources"]:
        month = month_from_name(r["name"].removesuffix(".csv"))
        if month and r.get("url"):
            found[month] = Resource(
                month, r["id"], r["name"], r["url"], r.get("last_modified"), r.get("size")
            )
    return dict(sorted(found.items()))


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_columns(found: list[str], wanted: dict[str, str], label: str) -> dict[str, str]:
    """Map this project's names to the file's column names; fail if one is missing."""
    mapping = {}
    for ours, pattern in wanted.items():
        hits = [c for c in found if re.fullmatch(pattern, _key(c))]
        if len(hits) != 1:
            raise SchemaMismatchError(f"{label}: expected one column like {ours!r}, found {hits}")
        mapping[ours] = hits[0]
    return mapping


def read_csv_sql(path: Path) -> str:
    return f"read_csv({sql_str(path)}, all_varchar = true, header = true, encoding = 'latin-1')"


def columns_of(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {read_csv_sql(path)}").fetchall()]


def write_parquet(con: duckdb.DuckDBPyConnection, select: str, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    con.execute(f"COPY ({select}) TO {sql_str(tmp)} (FORMAT parquet, COMPRESSION zstd)")
    (rows,) = con.execute(f"SELECT count(*) FROM read_parquet({sql_str(tmp)})").fetchone()
    tmp.rename(path)
    return rows


def filter_month(csv_path: Path, month: str, raw_dir: Path) -> dict:
    """Write the month's diabetes and practice files from a downloaded CSV."""
    con = duckdb.connect()
    c = match_columns(columns_of(con, csv_path), PRESCRIBING_COLUMNS, month)
    src = read_csv_sql(csv_path)

    def col(name):
        return f'"{c[name]}"'

    items = f"cast({col('items')} as double)"
    is_target = f"lower(trim({col('substance')})) = '{TARGET_SUBSTANCE}'"
    diabetes = f"""
        SELECT
            trim({col("practice")}) AS practice_code,
            trim({col("substance")}) AS substance,
            CASE WHEN {is_target}
                THEN coalesce(nullif(trim({col("product")}), '-'), trim({col("generic_product")}))
            END AS target_presentation,
            cast(sum({items}) AS varchar) AS items,
            cast(sum(cast({col("quantity")} as double)) AS varchar) AS quantity,
            cast(sum(cast({col("gross_cost")} as double)) AS varchar) AS gross_cost,
            cast(sum(cast({col("actual_cost")} as double)) AS varchar) AS actual_cost
        FROM {src}
        WHERE trim({col("bnf_chapter")}) = '6' AND trim({col("bnf_section")}) = '1'
            AND trim({col("bnf_paragraph")}) IN ('1', '2')
        GROUP BY 1, 2, 3"""
    practices = f"""
        SELECT trim({col("practice")}) AS practice_code, cast(sum({items}) AS varchar) AS items
        FROM {src}
        GROUP BY 1"""
    folder = f"year_month={month}"
    summary = {
        "columns": c,
        "diabetes_rows": write_parquet(
            con, diabetes, raw_dir / "ni_diabetes" / folder / "data.parquet"
        ),
        "practice_rows": write_parquet(
            con, practices, raw_dir / "ni_practices" / folder / "data.parquet"
        ),
    }
    # Every item in the file must be in the practices file: nothing lost in the grouping.
    (in_file,) = con.execute(f"SELECT sum({items}) FROM {src}").fetchone()
    saved = raw_dir / "ni_practices" / folder / "data.parquet"
    (kept,) = con.execute(
        f"SELECT sum(cast(items as double)) FROM read_parquet({sql_str(saved)})"
    ).fetchone()
    if round(in_file or 0) != round(kept or 0):
        raise RuntimeError(f"{month}: {in_file} items in the file, {kept} saved")
    summary["items"] = round(in_file or 0)
    return summary


def is_up_to_date(source: Path, resource: Resource) -> bool:
    return source.exists() and json.loads(source.read_text()).get("version") == resource.version


def ingest_month(
    client: httpx.Client, resource: Resource, raw_dir: Path = RAW_DIR, force: bool = False
) -> dict | None:
    source = raw_dir / "ni_diabetes" / f"year_month={resource.month}" / "_source.json"
    if not force and is_up_to_date(source, resource):
        log.info("%s is up to date; skipping", resource.month)
        return None
    with tempfile.TemporaryDirectory(dir=raw_dir) as tmp:
        csv_path = Path(tmp) / "month.csv"
        download_with_retries(client, resource.url, csv_path)
        summary = filter_month(csv_path, resource.month, raw_dir)
    summary |= {
        "month": resource.month,
        "resource_name": resource.name,
        "url": resource.url,
        "version": resource.version,
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    source.write_text(json.dumps(summary, indent=2) + "\n")
    log.info("%s: %s practices, %s diabetes rows", resource.month,
             f"{summary['practice_rows']:,}", f"{summary['diabetes_rows']:,}")  # fmt: skip
    return summary


def ingest_register(
    client: httpx.Client, resource: Resource, raw_dir: Path = RAW_DIR, force: bool = False
) -> int | None:
    folder = raw_dir / "ni_register" / f"year_month={resource.month}"
    source = folder / "_source.json"
    if not force and is_up_to_date(source, resource):
        return None
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=raw_dir) as tmp:
        csv_path = Path(tmp) / "register.csv"
        download_with_retries(client, resource.url, csv_path)
        con = duckdb.connect()
        c = match_columns(columns_of(con, csv_path), REGISTER_COLUMNS, resource.name)
        select = ", ".join(f'trim("{c[ours]}") AS {ours}' for ours in REGISTER_COLUMNS)
        rows = write_parquet(
            con, f"SELECT {select} FROM {read_csv_sql(csv_path)}", folder / "data.parquet"
        )
    summary = {
        "month": resource.month,
        "resource_name": resource.name,
        "version": resource.version,
        "columns": c,
        "rows": rows,
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    source.write_text(json.dumps(summary, indent=2) + "\n")
    log.info("register %s: %s practices", resource.month, f"{rows:,}")
    return rows


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
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with new_client() as client:
        registers = find_resources(client, REGISTER_PACKAGE)
        for month in select_months(list(registers), args.months):
            ingest_register(client, registers[month], force=args.force)
        months = find_resources(client, PRESCRIBING_PACKAGE)
        for month in select_months(list(months), args.months):
            ingest_month(client, months[month], force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
