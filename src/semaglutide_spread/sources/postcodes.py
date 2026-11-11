"""Look up the location and area of every UK GP practice postcode, using postcodes.io.

The UK prescribing data give each practice's postcode. postcodes.io (an open-source service
built on the ONS Postcode Directory, the official list of UK postcodes) turns a postcode into
a latitude/longitude and the areas it sits in: country, region, NHS integrated care board,
local authority, small area (LSOA) and the 2021 rural-urban classification.

Practices close and postcodes are retired, so a postcode that is no longer current is looked
up again as a terminated postcode, which still has a location (but no areas).

Steps:
1. Collect the distinct postcodes from the saved practice files (England's prescribing data
   and Scotland's practice register so far), tidied to the standard "AB1 2CD" form.
2. Load the results of earlier runs and keep only postcodes not yet looked up.
3. Look up current postcodes 100 at a time (the service's bulk limit).
4. Look up the ones not found as terminated postcodes, one at a time.
5. Save every result, found or not, as text-only Parquet, with a summary in `_source.json`.

Output: data/raw/postcodes/data.parquet (one row per postcode)

Usage:
    uv run postcode-lookup
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx

from semaglutide_spread.paths import RAW_DIR
from semaglutide_spread.sources.common import new_client, sql_str, with_retries

log = logging.getLogger(__name__)

API = "https://api.postcodes.io"
BATCH_SIZE = 100  # the most postcodes postcodes.io accepts in one bulk request
OUT_NAME = "postcodes"

# Practice files to read postcodes from: (folder under data/raw, postcode column).
PRACTICE_SOURCES = (
    ("epd_practices", "postcode"),
    ("phs_register", "postcode"),
    ("ni_register", "postcode"),
    ("wales_register", "postcode"),
)

POSTCODE = re.compile(r"^([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})$")


@dataclass
class PostcodeResult:
    postcode: str
    status: str  # "current", "terminated" or "not_found"
    latitude: str | None = None
    longitude: str | None = None
    country: str | None = None
    region: str | None = None
    icb_code: str | None = None
    icb_name: str | None = None
    local_authority_code: str | None = None
    local_authority_name: str | None = None
    lsoa21_code: str | None = None
    rural_urban_2021: str | None = None
    terminated: str | None = None  # YYYY-MM, for terminated postcodes
    looked_up_at: str | None = None


def normalise(raw: str | None) -> str | None:
    """'ls11aa ' -> 'LS1 1AA'. Returns None for values that aren't a UK postcode (e.g. '-')."""
    if raw is None:
        return None
    match = POSTCODE.match(raw.strip().upper())
    return f"{match[1]} {match[2]}" if match else None


def find_postcodes(raw_dir: Path = RAW_DIR) -> list[str]:
    """Distinct, tidied postcodes across every saved practice file."""
    found: set[str] = set()
    con = duckdb.connect()
    for folder, column in PRACTICE_SOURCES:
        files = raw_dir / folder / "*" / "data.parquet"
        if not list((raw_dir / folder).glob("*/data.parquet")):
            continue
        rows = con.execute(
            f'SELECT DISTINCT "{column}" FROM read_parquet({sql_str(files)})'
        ).fetchall()
        found |= {pc for (value,) in rows if (pc := normalise(value))}
    return sorted(found)


def to_result(postcode: str, data: dict | None, looked_up_at: str) -> PostcodeResult:
    """Turn a postcodes.io result into a row (None means not found)."""
    if data is None:
        return PostcodeResult(postcode, "not_found", looked_up_at=looked_up_at)
    codes = data.get("codes") or {}

    def text(value):
        return None if value is None else str(value)

    return PostcodeResult(
        postcode=postcode,
        status="current",
        latitude=text(data.get("latitude")),
        longitude=text(data.get("longitude")),
        country=data.get("country"),
        region=data.get("region"),
        icb_code=codes.get("icb"),
        icb_name=data.get("icb"),
        local_authority_code=codes.get("admin_district"),
        local_authority_name=data.get("admin_district"),
        lsoa21_code=codes.get("lsoa21"),
        rural_urban_2021=data.get("ruc21"),
        looked_up_at=looked_up_at,
    )


def lookup_current(client: httpx.Client, postcodes: list[str]) -> dict[str, dict | None]:
    """Bulk lookup: {postcode: result or None}."""

    def post():
        response = client.post(f"{API}/postcodes", json={"postcodes": postcodes})
        response.raise_for_status()
        return response.json()

    body = with_retries(post, f"postcode batch starting {postcodes[0]}")
    return {item["query"]: item["result"] for item in body["result"]}


def lookup_terminated(client: httpx.Client, postcode: str) -> dict | None:
    """A retired postcode's location, or None if postcodes.io has never heard of it."""

    def get():
        response = client.get(f"{API}/terminated_postcodes/{postcode}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["result"]

    return with_retries(get, f"terminated postcode {postcode}")


def load_cache(path: Path) -> dict[str, PostcodeResult]:
    if not path.exists():
        return {}
    names = [f.name for f in fields(PostcodeResult)]
    rows = duckdb.connect().execute(f"SELECT {', '.join(names)} FROM read_parquet({sql_str(path)})")
    return {r[0]: PostcodeResult(*r) for r in rows.fetchall()}


def save(results: dict[str, PostcodeResult], out_dir: Path, current: set[str]) -> None:
    """Write every result (kept even if no longer needed) and a summary of the current ones."""
    out_dir.mkdir(parents=True, exist_ok=True)
    names = [f.name for f in fields(PostcodeResult)]
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({', '.join(f'{n} VARCHAR' for n in names)})")
    con.executemany(
        f"INSERT INTO t VALUES ({', '.join('?' * len(names))})",
        [tuple(asdict(r).values()) for r in results.values()],
    )
    tmp = out_dir / "data.parquet.tmp"
    con.execute(f"COPY t TO {sql_str(tmp)} (FORMAT parquet, COMPRESSION zstd)")
    tmp.rename(out_dir / "data.parquet")
    needed = [r for pc, r in results.items() if pc in current]
    summary = {
        "source": API,
        "postcodes": len(needed),
        "postcodes_cached": len(results),
        "by_status": dict(Counter(r.status for r in needed)),
        "by_country": dict(Counter(r.country or "unknown" for r in needed)),
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "_source.json").write_text(json.dumps(summary, indent=2) + "\n")


def run(
    client: httpx.Client, raw_dir: Path = RAW_DIR, batch_size: int = BATCH_SIZE
) -> dict[str, PostcodeResult]:
    out_dir = raw_dir / OUT_NAME
    postcodes = find_postcodes(raw_dir)
    results = load_cache(out_dir / "data.parquet")
    todo = [pc for pc in postcodes if pc not in results]
    log.info("%d postcodes, %d to look up", len(postcodes), len(todo))
    now = datetime.now(UTC).isoformat(timespec="seconds")

    for start in range(0, len(todo), batch_size):
        batch = todo[start : start + batch_size]
        for postcode, data in lookup_current(client, batch).items():
            results[postcode] = to_result(postcode, data, now)

    for postcode in [pc for pc in todo if results[pc].status == "not_found"]:
        data = lookup_terminated(client, postcode)
        if data is not None:
            results[postcode] = PostcodeResult(
                postcode,
                "terminated",
                latitude=str(data["latitude"]),
                longitude=str(data["longitude"]),
                terminated=f"{data['year_terminated']}-{data['month_terminated']:02d}",
                looked_up_at=now,
            )

    save(results, out_dir, set(postcodes))
    return {pc: results[pc] for pc in postcodes}


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.split("\n")[0]).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    with new_client() as client:
        results = run(client)
    log.info("Statuses: %s", dict(Counter(r.status for r in results.values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
