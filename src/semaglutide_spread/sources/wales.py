"""Download Wales's diabetes prescribing by GP practice and month, from NWSSP.

NHS Wales Shared Services Partnership (NWSSP) publishes a "GP Data Extract" for each month:
a ~17 MB zip holding the month's prescribing (every product, every GP practice, ~60 MB of
CSV), the practice address file and two BNF lookups. Each zip has its own web page whose
address follows the month's name ("gp-data-extract-april-2018", occasionally the short name,
"gp-data-extract-dec-2025"), so months are found by building that address and checking that a
zip comes back, holding that month's file.

The public pages start in April 2018. January to March 2018 were sent by NWSSP in reply to a
request; pass the folder holding those zips (GPData2018MM.zip) with --local-dir.

Each month's zip is filtered on this computer with DuckDB, like Northern Ireland's files:
1. Diabetes drugs: one row per practice x BNF chemical substance (the first 9 characters of
   the 15-character BNF code), for BNF 6.1.1 and 6.1.2, with semaglutide rows split by
   product name. Wales uses the standard BNF codes, like England and Scotland.
2. Practices: every practice's total items.
3. The address file: each practice's name, address and postcode.
Every item in the file must be in the practices file, or the download stops. Files are
UTF-8 in some months and Latin-1 in others, so each is checked before it is read.

Outputs:
    data/raw/wales_diabetes/year_month=YYYY-MM/data.parquet (+ _source.json)
    data/raw/wales_practices/year_month=YYYY-MM/data.parquet
    data/raw/wales_register/year_month=YYYY-MM/data.parquet

Usage:
    uv run wales-download
    uv run wales-download --local-dir ~/Documents/semaglutide-spread-wales
    uv run wales-download --months 2019-01:2019-12
"""

from __future__ import annotations

import argparse
import calendar
import codecs
import hashlib
import json
import logging
import sys
import tempfile
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import httpx

from semaglutide_spread.paths import RAW_DIR
from semaglutide_spread.sources.common import download_with_retries, new_client, sql_str
from semaglutide_spread.sources.epd import DIABETES_SECTIONS, FIRST_MONTH, TARGET_CHEMICAL
from semaglutide_spread.sources.ni import match_columns, write_parquet

log = logging.getLogger(__name__)

PAGE = (
    "https://nwssp.nhs.wales/ourservices/primary-care-services/primary-care-services-documents/"
    "general-practice-prescribing-data-extract-docs/gp-data-extract-{name}-{year}"
)

PRESCRIBING_COLUMNS = {
    "health_board": r"hb",
    "practice": r"practiceid",
    "bnf_code": r"bnfcode",
    "bnf_name": r"bnfname",
    "items": r"items",
    "nic": r"nic",
    "actual_cost": r"actcost",
    "quantity": r"quantity",
}
ADDRESS_COLUMNS = {
    "practice_code": r"practiceid",
    "practice_name": r"street",  # the first address line holds the practice's name
    "address_2": r"area",
    "address_3": r"posttown",
    "address_4": r"county",
    "postcode": r"postcode",
}


def detect_encoding(path: Path) -> str:
    """'utf-8' if the whole file is valid UTF-8, else 'latin-1'. The extracts change
    encoding from month to month."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with path.open("rb") as f:
            while chunk := f.read(1 << 20):
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return "latin-1"
    return "utf-8"


def read_csv_sql(path: Path) -> str:
    encoding = detect_encoding(path)
    return f"read_csv({sql_str(path)}, all_varchar = true, header = true, encoding = '{encoding}')"


def columns_of(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {read_csv_sql(path)}").fetchall()]


class NotPublishedError(Exception):
    """The month's page exists but has no zip (not published, or no longer online)."""


def months_between(first: str, last: str) -> list[str]:
    """['2018-01', '2018-02', ...] from first to last, inclusive."""
    (y, m), (y2, m2) = (map(int, first.split("-")), map(int, last.split("-")))
    out = []
    while (y, m) <= (y2, m2):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def page_urls(month: str) -> list[str]:
    """The month's page address: usually the full month name ("december-2025"), but some
    months use the short name ("dec-2025"), so both are tried."""
    year, number = month.split("-")
    full, short = calendar.month_name[int(number)].lower(), calendar.month_abbr[int(number)].lower()
    return [PAGE.format(name=full, year=year), PAGE.format(name=short, year=year)]


def download_month(client: httpx.Client, month: str, dest: Path) -> str:
    """Download the month's zip to dest; return its URL. Raises NotPublishedError if no
    address gives back a zip (the site answers missing pages with an HTML page)."""
    for url in page_urls(month):
        download_with_retries(client, url, dest)
        if zipfile.is_zipfile(dest):
            return url
        dest.unlink()
    raise NotPublishedError(f"{month}: no zip at {' or '.join(page_urls(month))}")


def member(archive: zipfile.ZipFile, prefix: str) -> str:
    names = [n for n in archive.namelist() if Path(n).name.lower().startswith(prefix.lower())]
    if len(names) != 1:
        raise ValueError(f"expected one {prefix}* file in the zip, found {names}")
    return names[0]


def filter_zip(zip_path: Path, month: str, raw_dir: Path) -> dict:
    """Write the month's diabetes, practice and address files from one zip."""
    folder = f"year_month={month}"
    con = duckdb.connect()
    with tempfile.TemporaryDirectory(dir=zip_path.parent) as tmp, zipfile.ZipFile(zip_path) as z:
        data_name = member(z, "GPData")
        if month.replace("-", "") not in data_name:  # e.g. a page linking the wrong month
            raise ValueError(f"{month}: the zip holds {data_name}")
        data_csv = Path(z.extract(data_name, tmp))
        address_csv = Path(z.extract(member(z, "Address"), tmp))
        c = match_columns(columns_of(con, data_csv), PRESCRIBING_COLUMNS, month)
        src = read_csv_sql(data_csv)  # computed once: detecting the encoding reads the file

        def col(name):
            return f'trim("{c[name]}")'

        items = f"cast({col('items')} as double)"
        chemical = f"left({col('bnf_code')}, 9)"
        sections = ", ".join(f"'{s}'" for s in DIABETES_SECTIONS)
        diabetes = f"""
            SELECT
                {col("practice")} AS practice_code,
                any_value({col("health_board")}) AS health_board,
                {chemical} AS bnf_chemical_code,
                CASE WHEN {chemical} = '{TARGET_CHEMICAL}' THEN {col("bnf_name")} END
                    AS target_presentation,
                cast(sum({items}) AS varchar) AS items,
                cast(sum(cast({col("quantity")} as double)) AS varchar) AS quantity,
                cast(sum(cast({col("nic")} as double)) AS varchar) AS nic,
                cast(sum(cast({col("actual_cost")} as double)) AS varchar) AS actual_cost
            FROM {src}
            WHERE left({col("bnf_code")}, 6) IN ({sections})
            GROUP BY 1, 3, 4"""
        practices = f"""
            SELECT
                {col("practice")} AS practice_code,
                any_value({col("health_board")}) AS health_board,
                cast(sum({items}) AS varchar) AS items
            FROM {src}
            GROUP BY 1"""
        practices_path = raw_dir / "wales_practices" / folder / "data.parquet"
        summary = {
            "columns": c,
            "diabetes_rows": write_parquet(
                con, diabetes, raw_dir / "wales_diabetes" / folder / "data.parquet"
            ),
            "practice_rows": write_parquet(con, practices, practices_path),
        }
        (in_file,) = con.execute(f"SELECT sum({items}) FROM {src}").fetchone()
        (kept,) = con.execute(
            f"SELECT sum(cast(items as double)) FROM read_parquet({sql_str(practices_path)})"
        ).fetchone()
        if round(in_file or 0) != round(kept or 0):
            raise RuntimeError(f"{month}: {in_file} items in the file, {kept} saved")
        summary["items"] = round(in_file or 0)

        a = match_columns(columns_of(con, address_csv), ADDRESS_COLUMNS, f"{month} address")
        select = ", ".join(f'trim("{a[ours]}") AS {ours}' for ours in ADDRESS_COLUMNS)
        summary["address_rows"] = write_parquet(
            con,
            f"SELECT DISTINCT {select} FROM {read_csv_sql(address_csv)}",
            raw_dir / "wales_register" / folder / "data.parquet",
        )
    return summary


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def ingest_month(
    client: httpx.Client,
    month: str,
    raw_dir: Path = RAW_DIR,
    local_dir: Path | None = None,
    force: bool = False,
) -> dict | None:
    """Fetch (or read from local_dir), filter and save one month. Returns None if already
    saved; raises NotPublishedError if the month isn't available."""
    source = raw_dir / "wales_diabetes" / f"year_month={month}" / "_source.json"
    if source.exists() and not force:
        log.info("%s already saved; skipping", month)
        return None
    raw_dir.mkdir(parents=True, exist_ok=True)
    local = local_dir / f"GPData{month.replace('-', '')}.zip" if local_dir else None
    with tempfile.TemporaryDirectory(dir=raw_dir) as tmp:
        if local is not None and local.exists():
            zip_path, origin = local, f"local file {local.name} (sent by NWSSP)"
        else:
            zip_path = Path(tmp) / "month.zip"
            origin = download_month(client, month, zip_path)
        summary = filter_zip(zip_path, month, raw_dir)
        summary |= {
            "month": month,
            "source": origin,
            "zip_sha256": sha256(zip_path),
            "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    source.write_text(json.dumps(summary, indent=2) + "\n")
    log.info("%s: %s practices, %s diabetes rows", month,
             f"{summary['practice_rows']:,}", f"{summary['diabetes_rows']:,}")  # fmt: skip
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--months", help='range to fetch, e.g. "2019-01:2019-12"')
    parser.add_argument("--local-dir", type=Path, help="folder with zips sent by NWSSP")
    parser.add_argument("--force", action="store_true", help="download again even if saved")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    first, last = (args.months or f"{FIRST_MONTH}:{date.today():%Y-%m}").split(":")
    missing = []
    with new_client() as client:
        for month in months_between(first, last):
            try:
                ingest_month(client, month, local_dir=args.local_dir, force=args.force)
            except NotPublishedError:
                missing.append(month)
    if missing:
        log.warning("Not available: %s", ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
