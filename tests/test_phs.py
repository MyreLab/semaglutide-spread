import json
import re
from urllib.parse import parse_qs, urlparse

import duckdb
import httpx
import pytest

from semaglutide_spread.sources import phs

DIABETES = [
    {"practice_code": str(10000 + i), "bnf_chemical_code": "0601022B0", "items": i + 1}
    for i in range(5)
]
PRACTICES = [{"practice_code": str(10000 + i), "items": 100} for i in range(5)]


class FakePhs:
    """A fake opendata.nhs.scot: two catalogs, the register, and paged SQL queries."""

    def __init__(self, short_by: int = 0):
        self.sql: list[str] = []
        self.short_by = short_by

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        path = request.url.path
        if path.endswith("package_show"):
            names = {
                phs.PRESCRIBING_PACKAGE: [
                    ("Data by Prescriber Location \u2013 January 2019", "jan"),
                    ("Data by Prescriber Location - February 2019", "feb"),
                    ("Data by Dispenser Location - February 2019", "disp"),
                ],
                phs.REGISTER_PACKAGE: [("GP Practices and List sizes January 2019", "reg")],
            }[params["id"]]
            resources = [{"name": n, "id": i, "size": 1, "last_modified": "x"} for n, i in names]
            return httpx.Response(200, json={"result": {"resources": resources}})
        if path.endswith("datastore_search"):  # the register: old column names
            record = {"PracticeCode": 10000, "PracticeName": "Muirhead", "Listsize": 8643,
                      "AddressLine1": "a", "AddressLine2": "b", "AddressLine3": "c",
                      "AddressLine4": "", "Postcode": "DD2 5NH", "PracticeType": "GMS",
                      "HB": "S08000030"}  # fmt: skip
            return httpx.Response(
                200, json={"success": True, "result": {"records": [record], "total": 1}}
            )
        sql = params["sql"]
        self.sql.append(sql)
        rows = DIABETES if "bnf_chemical_code" in sql else PRACTICES
        if sql.startswith("SELECT SUM(items) AS items FROM ("):
            records = [{"items": str(sum(r["items"] for r in rows) + self.short_by)}]
        else:
            limit, offset = map(int, re.search(r"LIMIT (\d+) OFFSET (\d+)", sql).groups())
            records = rows[offset : offset + limit]
        return httpx.Response(200, json={"success": True, "result": {"records": records}})


def client(api):
    return httpx.Client(transport=httpx.MockTransport(api))


def test_reads_months_from_resource_names_with_either_dash():
    # PHS names some resources with an en dash (\u2013), others with a hyphen.
    assert phs.month_from_name("Data by Prescriber Location \u2013 July 2026") == "2026-07"
    assert phs.month_from_name("Data by Prescriber Location - December 2022") == "2022-12"
    assert phs.month_from_name("Data by Board \u2013 January - June 2026") == "2026-06"
    assert phs.month_from_name("Data dictionary") is None


def test_finds_only_prescriber_location_resources():
    with client(FakePhs()) as c:
        found = phs.find_resources(c, phs.PRESCRIBING_PACKAGE, "Data by Prescriber Location")
    assert list(found) == ["2019-01", "2019-02"]
    assert found["2019-02"].resource_id == "feb"


def test_fetches_pages_checks_totals_and_saves(tmp_path):
    api = FakePhs()
    with client(api) as c:
        resource = phs.find_resources(c, phs.PRESCRIBING_PACKAGE, "Data by Prescriber")["2019-01"]
        summary = phs.ingest_month(c, resource, raw_dir=tmp_path, page_size=2)
    assert summary["diabetes"]["rows"] == 5 and summary["diabetes"]["items"] == 15
    offsets = [re.search(r"OFFSET (\d+)", q).group(1) for q in api.sql if "OFFSET" in q]
    assert offsets == ["0", "2", "4"] * 2
    assert (tmp_path / "phs_practices" / "year_month=2019-01" / "data.parquet").exists()


def test_stops_if_pages_do_not_add_up(tmp_path):
    with client(FakePhs(short_by=2)) as c:
        resource = phs.find_resources(c, phs.PRESCRIBING_PACKAGE, "Data by Prescriber")["2019-01"]
        with pytest.raises(phs.TotalsMismatchError):
            phs.ingest_month(c, resource, raw_dir=tmp_path)


def test_register_columns_are_renamed_whatever_their_old_names(tmp_path):
    with client(FakePhs()) as c:
        resource = phs.find_resources(c, phs.REGISTER_PACKAGE, "GP Practices")["2019-01"]
        assert phs.ingest_register(c, resource, raw_dir=tmp_path) == 1
        assert phs.ingest_register(c, resource, raw_dir=tmp_path) is None  # already saved
    path = tmp_path / "phs_register" / "quarter=2019-01" / "data.parquet"
    row = duckdb.connect().execute(f"SELECT practice_name, list_size FROM '{path}'").fetchone()
    assert row == ("Muirhead", "8643")
    assert json.loads((path.parent / "_source.json").read_text())["rows"] == 1


def test_register_with_unknown_columns_fails_clearly():
    with pytest.raises(phs.SchemaMismatchError, match="practice_name"):
        phs.normalise_register_row({"PracticeCode": 1}, "test")
