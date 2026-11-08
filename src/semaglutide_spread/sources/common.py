"""Building blocks shared by every downloader: HTTP with retries, resumable downloads,
CSV-to-Parquet conversion, and UTF-8 repair."""

from __future__ import annotations

import csv
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import duckdb
import httpx

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(60.0, read=300.0)
# Identify the project to the servers we download from (some block anonymous clients).
HEADERS = {"User-Agent": "semaglutide-spread (+https://github.com/MyreLab/semaglutide-spread)"}
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB

RETRY_STATUSES = {429, 500, 502, 503, 504}  # "slow down" and temporary server errors
MAX_ATTEMPTS = 6


class SchemaMismatchError(Exception):
    """A downloaded file is missing columns that its publisher says it should have."""


def new_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS)


# --- Retrying ------------------------------------------------------------------------------


def with_retries[T](
    action: Callable[[], T], what: str, sleep: Callable[[float], None] = time.sleep
) -> T:
    """Run `action`, retrying temporary failures with exponential backoff (2, 4, 8, ... seconds).

    Retries network errors, rate limiting (429) and temporary server errors (5xx). If the
    server says how long to wait (a Retry-After header), that wait is used instead.
    Anything else, such as 404 Not Found, fails straight away.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return action()
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            if isinstance(error, httpx.HTTPStatusError):
                if error.response.status_code not in RETRY_STATUSES:
                    raise
                retry_after = error.response.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else 2.0**attempt
            else:
                wait = 2.0**attempt
            if attempt == MAX_ATTEMPTS:
                raise
            log.warning("%s failed (%s); retrying in %.0f s", what, error, wait)
            sleep(wait)
    raise AssertionError("unreachable")


def get_json(client: httpx.Client, url: str) -> dict:
    """GET a JSON document, retrying temporary failures."""

    def fetch() -> dict:
        response = client.get(url)
        response.raise_for_status()
        return response.json()

    return with_retries(fetch, url)


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


def download_with_retries(client: httpx.Client, url: str, dest: Path) -> None:
    """Download with retries; each retry resumes where the previous attempt stopped."""
    with_retries(lambda: download_file(client, url, dest), url)


# --- Checking and converting ---------------------------------------------------------------


def read_header(csv_path: Path, delimiter: str = ",") -> list[str]:
    """Column names from the first line, stripped of padding. Reads only that line, as bytes,
    so a broken byte further down can't trip this up."""
    with csv_path.open("rb") as f:
        first_line = f.readline().decode("utf-8-sig")
    return [name.strip() for name in next(csv.reader([first_line], delimiter=delimiter))]


def check_columns(
    found: list[str], expected: Iterable[str], source: str, warn_extra: bool = True
) -> None:
    """Fail if any expected column is missing. With `warn_extra`, also log columns that were
    not expected (useful when `expected` is a publisher's complete data dictionary)."""
    expected = list(expected)
    missing = [c for c in expected if c not in found]
    if missing:
        raise SchemaMismatchError(f"{source} is missing columns: {', '.join(missing)}")
    extra = [c for c in found if c not in set(expected)]
    if extra and warn_extra:
        log.warning("%s has columns not in the expected list: %s", source, ", ".join(extra))


def csv_to_parquet(csv_path: Path, parquet_path: Path, delimiter: str | None = None) -> int:
    """Convert a CSV to a zstd-compressed Parquet file, keeping every column as text.

    Column names are stripped of surrounding spaces (some Census files pad them). The
    delimiter is detected automatically unless given. Returns the number of rows written.
    """
    tmp = parquet_path.with_name(parquet_path.name + ".tmp")
    options = "header = true, all_varchar = true"
    if delimiter is not None:
        options += f", delim = {sql_str(delimiter)}"
    con = duckdb.connect()
    try:
        con.execute("SET preserve_insertion_order = false")  # lets DuckDB stream with less memory
        columns = [
            row[0]
            for row in con.execute(
                f"DESCRIBE SELECT * FROM read_csv({sql_str(csv_path)}, {options})"
            ).fetchall()
        ]
        select = ", ".join(f'"{c}" AS "{c.strip()}"' for c in columns)
        con.execute(
            f"""
            COPY (SELECT {select} FROM read_csv({sql_str(csv_path)}, {options}))
            TO {sql_str(tmp)} (FORMAT parquet, COMPRESSION zstd)
            """
        )
        (rows,) = con.execute(f"SELECT count(*) FROM read_parquet({sql_str(tmp)})").fetchone()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        con.close()
    tmp.rename(parquet_path)
    return rows


def repair_utf8(csv_path: Path) -> int:
    """Replace bytes that aren't valid UTF-8 with "�" (U+FFFD), line by line, in place.

    Public files are usually UTF-8 but can contain the odd corrupted line: the 2020 CMS "by
    Provider and Drug" file has one city name with broken bytes. Rather than dropping that row,
    the bad bytes become "�", the character CMS itself already uses elsewhere in its files.
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


def convert_csv(
    csv_path: Path, parquet_path: Path, label: str, delimiter: str | None = None
) -> tuple[int, int]:
    """Convert to Parquet, repairing invalid UTF-8 if (and only if) DuckDB rejects the file
    for that reason. Returns (rows written, lines repaired)."""
    try:
        return csv_to_parquet(csv_path, parquet_path, delimiter), 0
    except duckdb.InvalidInputException as error:
        if "unicode" not in str(error).lower():
            raise
    repaired = repair_utf8(csv_path)
    log.warning("%s: replaced invalid UTF-8 bytes on %d line(s)", label, repaired)
    return csv_to_parquet(csv_path, parquet_path, delimiter), repaired


def sql_str(value: Path | str) -> str:
    """Quote a value as a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"
