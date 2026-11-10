import json

import duckdb
import httpx
import pytest

from semaglutide_spread.sources import ni

# Shaped like the real file: Latin-1 text (the pound sign), BNF chapter/section/paragraph
# columns, and "-" for products prescribed by generic name.
HEADER = (
    "Practice,Year,Month,VTM_NM,VMP_NM,AMP_NM,Presentation,Strength,Total Items,Total Quantity,"
    "Gross Cost (£),Actual Cost (£),BNF Code,BNF Chapter,BNF Section,BNF Paragraph,"
    "BNF Sub-Paragraph"
)
ROWS = [
    "1,2026,9,Metformin,Metformin 500mg tablets,-,-,-,40,1000,20,19,06010203,6,1,2,0",
    "1,2026,9,Semaglutide,Semaglutide 1mg inj,Ozempic 1mg pens,-,-,3,3,200,199,060102,6,1,2,0",
    "1,2026,9,Semaglutide,Semaglutide 9mg tablets,-,-,-,2,60,150,149,060102,6,1,2,0",
    "2,2026,9,Insulin glargine,Insulin glargine pens,Lantus pens,-,-,5,5,50,49,060101,6,1,1,0",
    "2,2026,9,-,Blood glucose testing strips,TrueMetrix strips,-,-,9,450,90,89,060106,6,1,6,0",
    "2,2026,9,-,Dressing 10cm,PremierPore,-,-,4,40,8,8,20030100,20,3,1,0",
]
CSV = ("\n".join([HEADER, *ROWS]) + "\n").encode("latin-1")
REGISTER = (
    "PracNo,PracticeName,Address1,Address2,Address3,Postcode,LCG,Registered_Patients\n"
    "1,Dr. HOEY & PARTNERS   ,HARLAND,HOLYWOOD ARCHES,BELFAST,BT4 1NS,Belfast,6783\n"
).encode("latin-1")


def server(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("package_show"):
        package = request.url.params["id"]
        names = {
            ni.PRESCRIBING_PACKAGE: [("GP Prescribing Northern Ireland, September 2026", "m.csv")],
            ni.REGISTER_PACKAGE: [("GP Practice Reference File - July 2026.csv", "r.csv")],
        }[package]
        resources = [
            {"id": url, "name": name, "url": f"https://files.example/{url}", "size": 1}
            for name, url in names
        ]
        return httpx.Response(200, json={"result": {"resources": resources}})
    return httpx.Response(200, content=CSV if request.url.path.endswith("m.csv") else REGISTER)


def client():
    return httpx.Client(transport=httpx.MockTransport(server))


def test_keeps_insulins_and_antidiabetic_drugs_only(tmp_path):
    with client() as c:
        month = ni.find_resources(c, ni.PRESCRIBING_PACKAGE)["2026-09"]
        summary = ni.ingest_month(c, month, raw_dir=tmp_path)
    assert summary["items"] == 63  # every item in the file is in the practices file

    path = tmp_path / "ni_diabetes" / "year_month=2026-09" / "data.parquet"
    rows = (
        duckdb.connect()
        .execute(
            "SELECT practice_code, substance, target_presentation, items "
            f"FROM '{path}' ORDER BY ALL"
        )
        .fetchall()
    )
    assert rows == [
        ("1", "Metformin", None, "40.0"),
        ("1", "Semaglutide", "Ozempic 1mg pens", "3.0"),
        ("1", "Semaglutide", "Semaglutide 9mg tablets", "2.0"),  # generic: the VMP name
        ("2", "Insulin glargine", None, "5.0"),
    ]  # test strips (6.1.6) and dressings (chapter 20) left out


def test_skips_a_month_already_saved(tmp_path):
    with client() as c:
        month = ni.find_resources(c, ni.PRESCRIBING_PACKAGE)["2026-09"]
        ni.ingest_month(c, month, raw_dir=tmp_path)
        assert ni.ingest_month(c, month, raw_dir=tmp_path) is None


def test_register_columns_are_matched_by_name(tmp_path):
    with client() as c:
        register = ni.find_resources(c, ni.REGISTER_PACKAGE)["2026-07"]
        assert ni.ingest_register(c, register, raw_dir=tmp_path) == 1
    folder = tmp_path / "ni_register" / "year_month=2026-07"
    row = (
        duckdb.connect()
        .execute(
            f"SELECT practice_code, practice_name, postcode, registered_patients "
            f"FROM '{folder / 'data.parquet'}'"
        )
        .fetchone()
    )
    assert row == ("1", "Dr. HOEY & PARTNERS", "BT4 1NS", "6783")
    assert json.loads((folder / "_source.json").read_text())["columns"]["practice_code"] == "PracNo"


def test_missing_columns_fail_clearly():
    with pytest.raises(ni.SchemaMismatchError, match="substance"):
        ni.match_columns(["Practice", "Total Items"], ni.PRESCRIBING_COLUMNS, "test")
