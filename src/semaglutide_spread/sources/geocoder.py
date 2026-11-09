"""Geocode prescriber addresses whose ZIP code has no ZIP Code Tabulation Area (ZCTA).

Most prescribers are placed on the map at their ZCTA's centre point. About 3% have a ZIP code
with no ZCTA, mostly unique ZIP codes given to large medical centres (e.g. 44195, Cleveland
Clinic). This module sends those addresses to the Census Bureau's batch geocoder, which
returns a latitude/longitude and 2020 census county for each street address it can match.

Steps:
1. Find every distinct address (street lines, city, state, ZIP) in the Part D "by Provider"
   files whose ZIP code is not a ZCTA, in the 50 states, DC and the territories. Military mail
   (APO/FPO/DPO) and foreign addresses are left out: they have no US street location.
2. Skip addresses already in the cache from an earlier run.
3. Send the first street line (`Prscrbr_St1`) to the geocoder in batches.
4. For addresses that didn't match and have a second street line, send `Prscrbr_St2` instead:
   some records put a department or building in line 1 and the street in line 2.
5. Save every result, matched or not, to the cache, so no address is sent twice.

Inputs:  data/raw/partd_by_provider/year=*/data.parquet
         data/raw/census_zcta_gazetteer_2020/data.parquet
Output:  data/raw/census_geocoder/data.parquet (one row per address) and _source.json

Usage:
    uv run geocode-addresses
    uv run geocode-addresses --force    # discard the cache and geocode everything again
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
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

BATCH_URL = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
# Current street ranges give the best match rate. Results are reported in 2020 census
# geography, to match the 2020 ZCTA files used for everyone else.
BENCHMARK = "Public_AR_Current"
VINTAGE = "Census2020_Current"
BATCH_SIZE = 1000  # the service accepts up to 10,000; smaller batches fail and retry cheaply

MILITARY_STATES = ("AA", "AE", "AP")  # Armed Forces mail "states"


@dataclass(frozen=True)
class Address:
    st1: str | None
    st2: str | None
    city: str | None
    state: str | None
    zip5: str | None

    @property
    def address_id(self) -> str:
        """A stable ID for this exact address, used as the cache key."""
        text = "|".join((v or "").strip().upper() for v in asdict(self).values())
        return hashlib.md5(text.encode()).hexdigest()


@dataclass
class GeocodeResult:
    address_id: str
    st1: str | None
    st2: str | None
    city: str | None
    state: str | None
    zip5: str | None
    status: str  # Match, No_Match or Tie
    matched_line: str | None = None  # "st1" or "st2"
    match_type: str | None = None  # Exact or Non_Exact
    matched_address: str | None = None
    longitude: str | None = None
    latitude: str | None = None
    state_fips: str | None = None
    county_fips: str | None = None  # 5 digits: state + county
    tract: str | None = None
    block: str | None = None
    geocoded_at: str | None = None

    @classmethod
    def unmatched(cls, address: Address, status: str = "No_Match") -> GeocodeResult:
        return cls(address_id=address.address_id, **asdict(address), status=status)


COLUMNS = [f.name for f in fields(GeocodeResult)]


# --- Which addresses -----------------------------------------------------------------------


def find_unplaced_addresses(raw_dir: Path = RAW_DIR) -> list[Address]:
    """Distinct prescriber addresses whose ZIP code is not a 2020 ZCTA."""
    providers = raw_dir / "partd_by_provider" / "*" / "data.parquet"
    gazetteer = raw_dir / "census_zcta_gazetteer_2020" / "data.parquet"
    military = ", ".join(sql_str(s) for s in MILITARY_STATES)
    rows = duckdb.sql(
        f"""
        SELECT DISTINCT Prscrbr_St1, Prscrbr_St2, Prscrbr_City, Prscrbr_State_Abrvtn,
               Prscrbr_Zip5
        FROM read_parquet({sql_str(providers)})
        WHERE Prscrbr_Cntry = 'US'
          AND Prscrbr_State_Abrvtn NOT IN ({military})
          AND Prscrbr_Zip5 IS NOT NULL
          AND Prscrbr_Zip5 NOT IN (SELECT GEOID FROM read_parquet({sql_str(gazetteer)}))
        ORDER BY ALL
        """
    ).fetchall()
    return [Address(*row) for row in rows]


# --- Talking to the geocoder ---------------------------------------------------------------


def build_batch_file(batch: list[tuple[str, str]], address_by_id: dict[str, Address]) -> bytes:
    """The geocoder's input format: ID, street, city, state, ZIP; no header row."""
    out = io.StringIO()
    writer = csv.writer(out)
    for address_id, street in batch:
        a = address_by_id[address_id]
        writer.writerow([address_id, street, a.city or "", a.state or "", a.zip5 or ""])
    return out.getvalue().encode()


def parse_batch_response(text: str) -> dict[str, list[str]]:
    """Map each ID to its result fields. Rows come back in no particular order, and unmatched
    rows have only three fields: ID, input address, status."""
    return {row[0]: row for row in csv.reader(io.StringIO(text)) if row}


def geocode_batch(
    client: httpx.Client, batch: list[tuple[str, str]], address_by_id: dict[str, Address]
) -> dict[str, list[str]]:
    def send() -> dict[str, list[str]]:
        response = client.post(
            BATCH_URL,
            data={"benchmark": BENCHMARK, "vintage": VINTAGE},
            files={"addressFile": ("addresses.csv", build_batch_file(batch, address_by_id))},
        )
        response.raise_for_status()
        return parse_batch_response(response.text)

    return with_retries(send, f"geocoder batch of {len(batch)}")


def to_result(address: Address, row: list[str] | None, line: str, now: str) -> GeocodeResult:
    """Turn one response row into a result. A missing row counts as no match."""
    if row is None or len(row) < 3:
        return GeocodeResult.unmatched(address)
    status = row[2]
    if status != "Match" or len(row) < 12:
        return GeocodeResult.unmatched(address, status=status)
    longitude, latitude = row[5].split(",")
    return GeocodeResult(
        address_id=address.address_id,
        **asdict(address),
        status="Match",
        matched_line=line,
        match_type=row[3],
        matched_address=row[4],
        longitude=longitude,
        latitude=latitude,
        state_fips=row[8],
        county_fips=row[8] + row[9],
        tract=row[10],
        block=row[11],
        geocoded_at=now,
    )


def geocode_line(
    client: httpx.Client, addresses: list[Address], line: str, batch_size: int = BATCH_SIZE
) -> dict[str, GeocodeResult]:
    """Geocode `addresses` using street line `line` ("st1" or "st2")."""
    address_by_id = {a.address_id: a for a in addresses}
    jobs = [(a.address_id, getattr(a, line)) for a in addresses]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    results: dict[str, GeocodeResult] = {}
    for start in range(0, len(jobs), batch_size):
        batch = jobs[start : start + batch_size]
        log.info(
            "Geocoding %s, addresses %d-%d of %d", line, start + 1, start + len(batch), len(jobs)
        )
        rows = geocode_batch(client, batch, address_by_id)
        for address_id, _ in batch:
            results[address_id] = to_result(
                address_by_id[address_id], rows.get(address_id), line, now
            )
    return results


def geocode_addresses(
    client: httpx.Client, addresses: list[Address], batch_size: int = BATCH_SIZE
) -> dict[str, GeocodeResult]:
    """Line 1 first; then line 2 for addresses that didn't match and have one."""
    results = geocode_line(client, addresses, "st1", batch_size)
    retry = [a for a in addresses if results[a.address_id].status != "Match" and a.st2]
    if retry:
        for address_id, result in geocode_line(client, retry, "st2", batch_size).items():
            if result.status == "Match":
                results[address_id] = result
    return results


# --- Cache ---------------------------------------------------------------------------------


def load_cache(path: Path) -> dict[str, GeocodeResult]:
    if not path.exists():
        return {}
    rows = duckdb.sql(f"SELECT {', '.join(COLUMNS)} FROM read_parquet({sql_str(path)})").fetchall()
    return {row[0]: GeocodeResult(*row) for row in rows}


def save_cache(results: dict[str, GeocodeResult], out_dir: Path) -> None:
    """Write all results as text-only Parquet, plus a summary in _source.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "results.csv"
    parquet_path = out_dir / "data.parquet"
    tmp = out_dir / "data.parquet.tmp"
    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for result in sorted(results.values(), key=lambda r: r.address_id):
            writer.writerow(["" if v is None else v for v in asdict(result).values()])
    try:
        duckdb.sql(
            f"""
            COPY (SELECT * FROM read_csv({sql_str(csv_path)}, header = true, all_varchar = true))
            TO {sql_str(tmp)} (FORMAT parquet, COMPRESSION zstd)
            """
        )
    finally:
        csv_path.unlink(missing_ok=True)
    tmp.replace(parquet_path)

    statuses = Counter(r.status for r in results.values())
    lines = Counter(r.matched_line for r in results.values() if r.status == "Match")
    summary = {
        "url": BATCH_URL,
        "benchmark": BENCHMARK,
        "vintage": VINTAGE,
        "addresses": len(results),
        "by_status": dict(sorted(statuses.items())),
        "matched_on_line": dict(sorted(lines.items())),
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "_source.json").write_text(json.dumps(summary, indent=2) + "\n")


def run(
    client: httpx.Client, raw_dir: Path = RAW_DIR, force: bool = False, batch_size: int = BATCH_SIZE
) -> dict[str, GeocodeResult]:
    out_dir = raw_dir / "census_geocoder"
    cache = {} if force else load_cache(out_dir / "data.parquet")
    addresses = find_unplaced_addresses(raw_dir)
    new = [a for a in addresses if a.address_id not in cache]
    log.info(
        "%d addresses need geocoding; %d already in the cache", len(new), len(addresses) - len(new)
    )
    if new:
        cache.update(geocode_addresses(client, new, batch_size))
        save_cache(cache, out_dir)
    return cache


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--force", action="store_true", help="ignore the cache")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with new_client() as client:
        results = run(client, force=args.force)
    statuses = Counter(r.status for r in results.values())
    log.info("Done: %s", ", ".join(f"{k} {v:,}" for k, v in sorted(statuses.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
