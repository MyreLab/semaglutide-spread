import json
from pathlib import Path

import duckdb
import httpx
import pytest

from semaglutide_spread.sources import partd

FIXTURES = Path(__file__).parent / "fixtures"

SAMPLE_CSV = (
    "Prscrbr_NPI,Prscrbr_State_FIPS,Gnrc_Name,Tot_Clms\n"
    "1003000126,06,Semaglutide,11\n"
    "1003000142,01,Metformin HCl,250\n"
    "1003000167,48,Semaglutide,\n"
)
SAMPLE_COLUMNS = ["Prscrbr_NPI", "Prscrbr_State_FIPS", "Gnrc_Name", "Tot_Clms"]


@pytest.fixture
def catalog():
    return json.loads((FIXTURES / "cms_catalog_sample.json").read_text())


# --- find_releases -------------------------------------------------------------------------


def test_find_releases_returns_one_release_per_year(catalog):
    releases = partd.find_releases(catalog, "by_provider_and_drug")
    assert sorted(releases) == [2023, 2024]
    assert releases[2024].csv_url.endswith("DY24_NPIBN.csv")
    assert releases[2024].dictionary_url.endswith("bbb/dictionary")
    assert releases[2024].modified == "2026-05-21"


def test_find_releases_does_not_mix_up_similar_titles(catalog):
    # "by Provider" is a prefix of "by Provider and Drug"; each must find only its own files.
    releases = partd.find_releases(catalog, "by_provider")
    assert list(releases) == [2024]
    assert releases[2024].csv_url.endswith("DY24_NPI.csv")


# --- check_columns -------------------------------------------------------------------------


def test_check_columns_passes_when_all_expected_present():
    partd.check_columns(SAMPLE_COLUMNS, SAMPLE_COLUMNS, "sample.csv")


def test_check_columns_fails_when_a_column_is_missing():
    with pytest.raises(partd.SchemaMismatchError, match="Tot_Clms"):
        partd.check_columns(SAMPLE_COLUMNS[:-1], SAMPLE_COLUMNS, "sample.csv")


# --- csv_to_parquet ------------------------------------------------------------------------


def test_csv_to_parquet_keeps_every_value_as_text(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(SAMPLE_CSV)
    parquet_path = tmp_path / "sample.parquet"

    rows = partd.csv_to_parquet(csv_path, parquet_path)

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
        partd.download_file(client, "https://example.org/file.csv", dest)
    assert dest.read_bytes() == CONTENT
    assert not (tmp_path / "file.csv.part").exists()


def test_download_file_resumes_a_partial_download(tmp_path):
    dest = tmp_path / "file.csv"
    (tmp_path / "file.csv.part").write_bytes(CONTENT[:4000])
    with httpx.Client(transport=httpx.MockTransport(_range_server)) as client:
        partd.download_file(client, "https://example.org/file.csv", dest)
    assert dest.read_bytes() == CONTENT


# --- ingest_release ------------------------------------------------------------------------


def _release(year=2024):
    return partd.Release(
        dataset="by_provider_and_drug",
        year=year,
        csv_url="https://example.org/data.csv",
        dictionary_url="https://example.org/dictionary",
        modified="2026-05-21",
    )


def _cms_server(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/dictionary":
        fields = [{"name": c} for c in SAMPLE_COLUMNS]
        return httpx.Response(200, json={"data": {"fields": fields}})
    return httpx.Response(200, content=SAMPLE_CSV.encode())


def test_ingest_release_saves_parquet_and_metadata_and_removes_csv(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(_cms_server)) as client:
        rows = partd.ingest_release(client, _release(), raw_dir=tmp_path)

    out = tmp_path / "partd_by_provider_and_drug" / "year=2024"
    assert rows == 3
    assert sorted(p.name for p in out.iterdir()) == ["_source.json", "data.parquet"]
    metadata = json.loads((out / "_source.json").read_text())
    assert metadata["rows"] == 3
    assert metadata["columns"] == SAMPLE_COLUMNS


def test_ingest_release_skips_years_already_saved(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(_cms_server)) as client:
        partd.ingest_release(client, _release(), raw_dir=tmp_path)

    def fail(request):
        raise AssertionError("should not download")

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        assert partd.ingest_release(client, _release(), raw_dir=tmp_path) is None


def test_ingest_release_downloads_again_when_cms_republishes_a_year(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(_cms_server)) as client:
        partd.ingest_release(client, _release(), raw_dir=tmp_path)
        republished = partd.Release(
            dataset="by_provider_and_drug",
            year=2024,
            csv_url="https://example.org/data-v2.csv",
            dictionary_url="https://example.org/dictionary",
            modified="2027-05-20",
        )
        assert partd.ingest_release(client, republished, raw_dir=tmp_path) == 3

    out = tmp_path / "partd_by_provider_and_drug" / "year=2024"
    assert json.loads((out / "_source.json").read_text())["csv_url"].endswith("data-v2.csv")


def test_ingest_release_downloads_again_if_metadata_is_missing(tmp_path):
    # A Parquet file without its _source.json can't be trusted (e.g. an interrupted run).
    out = tmp_path / "partd_by_provider_and_drug" / "year=2024"
    out.mkdir(parents=True)
    (out / "data.parquet").write_bytes(b"unknown origin")
    with httpx.Client(transport=httpx.MockTransport(_cms_server)) as client:
        assert partd.ingest_release(client, _release(), raw_dir=tmp_path) == 3


def test_select_years_defaults_to_every_published_year_from_2018():
    # When CMS publishes 2025, it is picked up without any code change.
    assert partd.select_years([2016, 2017, 2018, 2024, 2025], None) == [2018, 2024, 2025]


def test_select_years_rejects_unpublished_years():
    with pytest.raises(ValueError, match="2026"):
        partd.select_years([2018, 2024, 2025], [2025, 2026])


def test_ingest_release_cleans_up_when_columns_are_wrong(tmp_path):
    def server(request):
        if request.url.path == "/dictionary":
            return httpx.Response(200, json={"data": {"fields": [{"name": "Not_A_Column"}]}})
        return httpx.Response(200, content=SAMPLE_CSV.encode())

    with (
        httpx.Client(transport=httpx.MockTransport(server)) as client,
        pytest.raises(partd.SchemaMismatchError),
    ):
        partd.ingest_release(client, _release(), raw_dir=tmp_path)

    out = tmp_path / "partd_by_provider_and_drug" / "year=2024"
    assert list(out.iterdir()) == []  # no CSV left behind, no Parquet written


def test_partitioned_years_read_back_with_a_year_column(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(_cms_server)) as client:
        for year in (2023, 2024):
            partd.ingest_release(client, _release(year), raw_dir=tmp_path)

    pattern = tmp_path / "partd_by_provider_and_drug" / "*" / "*.parquet"
    counts = (
        duckdb.connect()
        .execute(
            f"SELECT year, count(*) FROM read_parquet('{pattern}', hive_partitioning = true) "
            "GROUP BY year ORDER BY year"
        )
        .fetchall()
    )
    assert counts == [(2023, 3), (2024, 3)]


# --- parse_years ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2024", [2024]),
        ("2018-2020", [2018, 2019, 2020]),
        ("2019,2021", [2019, 2021]),
        ("2018-2019,2024", [2018, 2019, 2024]),
    ],
)
def test_parse_years(text, expected):
    assert partd.parse_years(text) == expected


# --- invalid UTF-8 -------------------------------------------------------------------------

# A row shaped like the real one in the 2020 file: the city name contains bytes that are not
# valid UTF-8. Another row uses CMS's own "�" character, which is valid and must stay as is.
BROKEN_CSV = (
    b"Prscrbr_NPI,Prscrbr_State_FIPS,Gnrc_Name,Tot_Clms\n"
    b"1811527914,72,Mayag\xe3\x9cez,35\n"
    b"1407855091,72,Concepci\xef\xbf\xbdn,12\n"
    b"1003000126,06,Semaglutide,11\n"
)


def test_repair_utf8_replaces_only_the_broken_bytes(tmp_path):
    csv_path = tmp_path / "broken.csv"
    csv_path.write_bytes(BROKEN_CSV)

    assert partd.repair_utf8(csv_path) == 1

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "1811527914,72,Mayag�ez,35"
    assert lines[2] == "1407855091,72,Concepci�n,12"
    assert lines[3] == "1003000126,06,Semaglutide,11"


def test_ingest_release_repairs_invalid_utf8_instead_of_dropping_rows(tmp_path):
    def server(request):
        if request.url.path == "/dictionary":
            fields = [{"name": c} for c in SAMPLE_COLUMNS]
            return httpx.Response(200, json={"data": {"fields": fields}})
        return httpx.Response(200, content=BROKEN_CSV)

    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        rows = partd.ingest_release(client, _release(), raw_dir=tmp_path)

    out = tmp_path / "partd_by_provider_and_drug" / "year=2024"
    assert rows == 3
    assert json.loads((out / "_source.json").read_text())["repaired_lines"] == 1


# --- retries -------------------------------------------------------------------------------


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
        result = partd.with_retries(
            lambda: partd._get_json(client, "https://example.org/x"), "x", sleep=waits.append
        )
    assert result == {"ok": True}
    assert calls["n"] == 3
    assert waits == [2.0, 4.0]  # exponential backoff


def test_with_retries_respects_retry_after_header():
    handler, _ = _flaky(failures=1, headers={"Retry-After": "30"})
    waits = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        partd.with_retries(
            lambda: partd._get_json(client, "https://example.org/x"), "x", sleep=waits.append
        )
    assert waits == [30.0]


def test_with_retries_does_not_retry_permanent_errors():
    handler, calls = _flaky(failures=1, status=404)
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        partd.with_retries(
            lambda: partd._get_json(client, "https://example.org/x"), "x", sleep=lambda s: None
        )
    assert calls["n"] == 1


def test_with_retries_gives_up_eventually():
    handler, calls = _flaky(failures=100, status=503)
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        partd.with_retries(
            lambda: partd._get_json(client, "https://example.org/x"), "x", sleep=lambda s: None
        )
    assert calls["n"] == partd.MAX_ATTEMPTS
