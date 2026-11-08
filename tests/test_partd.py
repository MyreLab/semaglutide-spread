import json
from pathlib import Path

import duckdb
import httpx
import pytest

from semaglutide_spread.sources import partd
from semaglutide_spread.sources.common import SchemaMismatchError

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
        pytest.raises(SchemaMismatchError),
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
