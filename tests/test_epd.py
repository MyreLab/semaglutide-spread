import json
import re
from urllib.parse import parse_qs, urlparse

import duckdb
import httpx
import pytest

from semaglutide_spread.sources import epd

# --- A fake NHSBSA API ---------------------------------------------------------------------

CATALOGS = {
    epd.RETIRED.package: [
        ("EPD_202009", "r-202009"),
        ("EPD_202010", "r-202010"),
        ("EPD_202011", "r-202011"),  # also in the SNOMED dataset, which is preferred
    ],
    epd.SNOMED.package: [("EPD_SNOMED_202011", "s-202011"), ("EPD_SNOMED_202012", "s-202012")],
}

PRACTICE_ROWS = [
    {
        "practice_code": f"P{i:04d}",
        "practice_name": f"Practice {i}",
        "postcode": "LS1 1AA",
        "items": 10,
    }
    for i in range(7)
]
DIABETES_ROWS = [
    {"practice_code": f"P{i:04d}", "bnf_chemical_code": "0601022B0", "items": i + 1}
    for i in range(7)
]


class FakeApi:
    """Answers catalog requests and the three kinds of SQL query, honouring LIMIT/OFFSET."""

    def __init__(self, short_by: int = 0):
        self.queries: list[tuple[str, str]] = []
        self.short_by = short_by  # make the total disagree with the pages

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        if request.url.path.endswith("package_show"):
            resources = [
                {"name": name, "id": rid, "size": 100, "created": "2026-01-01"}
                for name, rid in CATALOGS[params["id"]]
            ] + [{"name": "Data dictionary", "id": "x"}]
            return httpx.Response(200, json={"result": {"resources": resources}})

        sql = params["sql"]
        self.queries.append((params["resource_id"], sql))
        if sql.startswith("SELECT * FROM"):  # the layout probe: one row with the column names
            renamed = params["resource_id"] >= "EPD_SNOMED_202012"
            layout = epd.RENAMED if renamed else epd.ORIGINAL
            names = (layout.chemical_code, layout.chemical_name, layout.presentation_name)
            row = dict.fromkeys(epd.SHARED_COLUMNS + names, "x")
            return httpx.Response(
                200, json={"success": True, "result": {"result": {"records": [row]}}}
            )
        rows = DIABETES_ROWS if "bnf_chemical_code" in sql else PRACTICE_ROWS
        if sql.startswith("SELECT SUM(items) AS items FROM ("):
            records = [{"items": sum(r["items"] for r in rows) + self.short_by}]
        else:
            limit, offset = map(int, re.search(r"LIMIT (\d+) OFFSET (\d+)", sql).groups())
            records = rows[offset : offset + limit]
        return httpx.Response(
            200, json={"success": True, "result": {"result": {"records": records}}}
        )


def client(api):
    return httpx.Client(transport=httpx.MockTransport(api))


# --- Tests ---------------------------------------------------------------------------------


def test_prefers_the_snomed_dataset_where_both_have_a_month():
    with client(FakeApi()) as c:
        releases = epd.find_releases(c)
    assert list(releases) == ["2020-09", "2020-10", "2020-11", "2020-12"]
    assert releases["2020-10"].resource_name == "EPD_202010"
    assert releases["2020-11"].resource_name == "EPD_SNOMED_202011"


def test_selects_from_first_month_or_a_given_range():
    months = ["2017-12", "2018-01", "2018-02", "2018-03"]
    assert epd.select_months(months) == ["2018-01", "2018-02", "2018-03"]
    assert epd.select_months(months, "2018-02:2018-02") == ["2018-02"]


def test_detects_each_months_column_names():
    with client(FakeApi()) as c:
        releases = epd.find_releases(c)
        original = epd.detect_layout(c, releases["2020-11"])
        renamed = epd.detect_layout(c, releases["2020-12"])
    assert original.layout is epd.ORIGINAL and renamed.layout is epd.RENAMED
    old_sql, _ = epd.diabetes_query(original)
    new_sql, _ = epd.diabetes_query(renamed)
    assert "CHEMICAL_SUBSTANCE_BNF_DESCR AS bnf_chemical_name" in old_sql
    assert "BNF_CHEMICAL_SUBSTANCE AS bnf_chemical_name" in new_sql
    assert "IN ('060101', '060102')" in new_sql  # insulins and antidiabetic drugs only


def test_fails_clearly_on_unknown_columns():
    def odd(request):
        row = {"PRACTICE_CODE": "x", "SOMETHING_NEW": "y"}
        return httpx.Response(200, json={"success": True, "result": {"result": {"records": [row]}}})

    with client(FakeApi()) as c:
        release = epd.find_releases(c)["2020-11"]
    with client(odd) as c, pytest.raises(epd.SchemaMismatchError, match="unknown columns"):
        epd.detect_layout(c, release)


def test_fetches_every_page_and_saves(tmp_path):
    api = FakeApi()
    with client(api) as c:
        release = epd.find_releases(c)["2020-11"]
        summary = epd.ingest_month(c, release, raw_dir=tmp_path, page_size=3)

    # 7 rows in pages of 3: 3 + 3 + 1, after one total query; for each of the two tables.
    pages = [sql for _, sql in api.queries if "OFFSET" in sql]
    assert [re.search(r"OFFSET (\d+)", q).group(1) for q in pages] == ["0", "3", "6"] * 2
    assert summary["diabetes"] == {**summary["diabetes"], "rows": 7, "items": 28}

    con = duckdb.connect()
    path = tmp_path / "epd_diabetes" / "year_month=2020-11" / "data.parquet"
    rows = con.execute(f"SELECT practice_code, items FROM '{path}' ORDER BY 1").fetchall()
    assert rows[0] == ("P0000", "1")  # stored as text, like all raw data
    source = json.loads((path.parent / "_source.json").read_text())
    assert source["version"]["resource_id"] == "s-202011"


def test_stops_if_pages_do_not_add_up(tmp_path):
    with client(FakeApi(short_by=1)) as c:
        release = epd.find_releases(c)["2020-11"]
        with pytest.raises(epd.TotalsMismatchError, match="expected 71"):
            epd.ingest_month(c, release, raw_dir=tmp_path, page_size=3)
    assert not (tmp_path / "epd_diabetes" / "year_month=2020-11" / "_source.json").exists()


def test_skips_a_month_already_saved_from_the_same_resource(tmp_path):
    with client(FakeApi()) as c:
        release = epd.find_releases(c)["2020-12"]
        epd.ingest_month(c, release, raw_dir=tmp_path)

    def fail(request):
        raise AssertionError("should not query")

    with client(fail) as c:
        assert epd.ingest_month(c, release, raw_dir=tmp_path) is None


def test_downloads_again_when_a_month_is_republished(tmp_path):
    with client(FakeApi()) as c:
        release = epd.find_releases(c)["2020-12"]
        epd.ingest_month(c, release, raw_dir=tmp_path)
    republished = epd.Release(**{**release.__dict__, "resource_id": "s-202012-v2"})
    assert not epd.is_up_to_date(republished, tmp_path)


def test_refuses_a_truncated_result():
    def truncated(request):
        body = {"success": True, "result": {"result": {"records_truncated": "true"}}}
        return httpx.Response(200, json=body)

    with client(FakeApi()) as c:
        release = epd.find_releases(c)["2020-11"]
    with client(truncated) as c, pytest.raises(RuntimeError, match="truncated"):
        epd.run_sql(c, release, "SELECT 1")
