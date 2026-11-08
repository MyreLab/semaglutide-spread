import duckdb
import httpx
import pytest

from semaglutide_spread.sources import common

SAMPLE_CSV = (
    "Prscrbr_NPI,Prscrbr_State_FIPS,Gnrc_Name,Tot_Clms\n"
    "1003000126,06,Semaglutide,11\n"
    "1003000142,01,Metformin HCl,250\n"
    "1003000167,48,Semaglutide,\n"
)
SAMPLE_COLUMNS = ["Prscrbr_NPI", "Prscrbr_State_FIPS", "Gnrc_Name", "Tot_Clms"]

# A row shaped like the real one in the 2020 CMS file: the city name contains bytes that are
# not valid UTF-8. Another row uses CMS's own "�" character, which is valid and must stay.
BROKEN_CSV = (
    b"Prscrbr_NPI,Prscrbr_State_FIPS,Gnrc_Name,Tot_Clms\n"
    b"1811527914,72,Mayag\xe3\x9cez,35\n"
    b"1407855091,72,Concepci\xef\xbf\xbdn,12\n"
    b"1003000126,06,Semaglutide,11\n"
)


# --- check_columns -------------------------------------------------------------------------


def test_check_columns_passes_when_all_expected_present():
    common.check_columns(SAMPLE_COLUMNS, SAMPLE_COLUMNS, "sample.csv")


def test_check_columns_fails_when_a_column_is_missing():
    with pytest.raises(common.SchemaMismatchError, match="Tot_Clms"):
        common.check_columns(SAMPLE_COLUMNS[:-1], SAMPLE_COLUMNS, "sample.csv")


# --- csv_to_parquet ------------------------------------------------------------------------


def test_csv_to_parquet_keeps_every_value_as_text(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(SAMPLE_CSV)
    parquet_path = tmp_path / "sample.parquet"

    rows = common.csv_to_parquet(csv_path, parquet_path)

    assert rows == 3
    assert not parquet_path.with_name("sample.parquet.tmp").exists()
    con = duckdb.connect()
    types = {
        name: kind
        for name, kind, *_ in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{parquet_path}')"
        ).fetchall()
    }
    assert set(types.values()) == {"VARCHAR"}
    fips = [
        r[0]
        for r in con.execute(
            f"SELECT Prscrbr_State_FIPS FROM read_parquet('{parquet_path}') ORDER BY 1"
        ).fetchall()
    ]
    assert fips == ["01", "06", "48"]  # leading zeros survive


# --- download_file -------------------------------------------------------------------------

CONTENT = b"0123456789" * 1000


def _range_server(request: httpx.Request) -> httpx.Response:
    """A fake web server that supports resuming downloads, like data.cms.gov does."""
    range_header = request.headers.get("Range")
    if not range_header:
        return httpx.Response(200, content=CONTENT)
    start = int(range_header.removeprefix("bytes=").rstrip("-"))
    if start >= len(CONTENT):
        return httpx.Response(416)
    return httpx.Response(206, content=CONTENT[start:])


def test_download_file_writes_complete_file(tmp_path):
    dest = tmp_path / "file.csv"
    with httpx.Client(transport=httpx.MockTransport(_range_server)) as client:
        common.download_file(client, "https://example.org/file.csv", dest)
    assert dest.read_bytes() == CONTENT
    assert not (tmp_path / "file.csv.part").exists()


def test_download_file_resumes_a_partial_download(tmp_path):
    dest = tmp_path / "file.csv"
    (tmp_path / "file.csv.part").write_bytes(CONTENT[:4000])
    with httpx.Client(transport=httpx.MockTransport(_range_server)) as client:
        common.download_file(client, "https://example.org/file.csv", dest)
    assert dest.read_bytes() == CONTENT


def test_repair_utf8_replaces_only_the_broken_bytes(tmp_path):
    csv_path = tmp_path / "broken.csv"
    csv_path.write_bytes(BROKEN_CSV)

    assert common.repair_utf8(csv_path) == 1

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "1811527914,72,Mayag�ez,35"
    assert lines[2] == "1407855091,72,Concepci�n,12"
    assert lines[3] == "1003000126,06,Semaglutide,11"


# --- retries -------------------------------------------------------------------------------


def _raw_get(client, url):
    response = client.get(url)
    response.raise_for_status()
    return response.json()


def _flaky(failures, status=429, headers=None):
    """A fake server that fails `failures` times with `status`, then succeeds."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] <= failures:
            return httpx.Response(status, headers=headers or {})
        return httpx.Response(200, json={"ok": True})

    return handler, calls


def test_with_retries_recovers_from_rate_limiting():
    handler, calls = _flaky(failures=2)
    waits = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = common.with_retries(
            lambda: _raw_get(client, "https://example.org/x"), "x", sleep=waits.append
        )
    assert result == {"ok": True}
    assert calls["n"] == 3
    assert waits == [2.0, 4.0]  # exponential backoff


def test_with_retries_respects_retry_after_header():
    handler, _ = _flaky(failures=1, headers={"Retry-After": "30"})
    waits = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        common.with_retries(
            lambda: _raw_get(client, "https://example.org/x"), "x", sleep=waits.append
        )
    assert waits == [30.0]


def test_with_retries_does_not_retry_permanent_errors():
    handler, calls = _flaky(failures=1, status=404)
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        common.with_retries(
            lambda: _raw_get(client, "https://example.org/x"), "x", sleep=lambda s: None
        )
    assert calls["n"] == 1


def test_with_retries_gives_up_eventually():
    handler, calls = _flaky(failures=100, status=503)
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        common.with_retries(
            lambda: _raw_get(client, "https://example.org/x"), "x", sleep=lambda s: None
        )
    assert calls["n"] == common.MAX_ATTEMPTS
