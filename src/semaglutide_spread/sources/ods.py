"""Download the list of GP practices from the NHS Organisation Data Service (ODS).

England's prescribing data list every "prescribing cost centre": GP practices, but also
walk-in centres, out-of-hours services, prisons, community teams and more. Their codes can't
be told apart reliably (new GP practices get "Y" codes, like many other services), so this
project asks the official register, ODS, which cost centres are GP practices.

The ODS API ("ORD") is searched for organisations whose main role is "prescribing cost
centre" (RO177) and which also have the role "GP practice" (RO76), open or closed (closed
practices are needed for the early years). Results come 1,000 at a time.

Output: data/raw/ods_gp_practices/data.parquet (one row per practice code)

Usage:
    uv run ods-download
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx

from semaglutide_spread.paths import RAW_DIR
from semaglutide_spread.sources.common import new_client, with_retries
from semaglutide_spread.sources.epd import save_rows

log = logging.getLogger(__name__)

API = "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations"
PRESCRIBING_COST_CENTRE = "RO177"
GP_PRACTICE = "RO76"
PAGE_SIZE = 1000
COLUMNS = ("OrgId", "Name", "Status", "PostCode", "LastChangeDate")


def fetch_page(client: httpx.Client, offset: int, page_size: int) -> tuple[list[dict], int]:
    """One page of GP practices, and the total number the API says there are."""
    params = {
        "PrimaryRoleId": PRESCRIBING_COST_CENTRE,
        "NonPrimaryRoleId": GP_PRACTICE,
        "Limit": page_size,
    }
    if offset:  # the API counts from 0 but rejects "Offset=0" (406), so leave it out
        params["Offset"] = offset
    url = f"{API}?" + urlencode(params)

    def get():
        response = client.get(url)
        response.raise_for_status()
        return response

    response = with_retries(get, f"ODS page at {offset}")
    return response.json()["Organisations"], int(response.headers["X-Total-Count"])


def run(client: httpx.Client, raw_dir: Path = RAW_DIR, page_size: int = PAGE_SIZE) -> int:
    rows: list[dict] = []
    total = None
    while total is None or len(rows) < total:
        page, total = fetch_page(client, len(rows), page_size)
        if not page:
            break
        rows += page
    codes = {r["OrgId"] for r in rows}
    if len(codes) != total:
        raise RuntimeError(f"ODS: got {len(codes):,} distinct practices; the API reports {total:,}")

    out_dir = raw_dir / "ods_gp_practices"
    written = save_rows(rows, COLUMNS, out_dir / "data.parquet")
    summary = {
        "source": API,
        "query": {"PrimaryRoleId": PRESCRIBING_COST_CENTRE, "NonPrimaryRoleId": GP_PRACTICE},
        "rows": written,
        "active": sum(r["Status"] == "Active" for r in rows),
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "_source.json").write_text(json.dumps(summary, indent=2) + "\n")
    log.info("%s GP practices (%s active)", f"{written:,}", f"{summary['active']:,}")
    return written


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.split("\n")[0]).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    with new_client() as client:
        run(client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
