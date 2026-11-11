import io
import json
import zipfile

import duckdb
import httpx
import pytest

from semaglutide_spread.sources import wales

DATA = (
    "HB,Locality,PracticeID,BNFCode,BNFName,Items,NIC,ActCost,Quantity,DDD,ADQ,Period\n"
    "7A6,6A1,W93001,0601022B0AAABAB,Metformin_Tab 500mg,30,15,14,1680,0,0,201804\n"
    "7A6,6A1,W93001,0601023AWAAAAAA,Semaglutide_Inj 1mg/0.74ml,2,146,140,2,0,0,201804\n"
    "7A6,6A1,W93001,0601023AWBBACAC,Ozempic_Inj 1mg/0.74ml,1,73,70,1,0,0,201804\n"
    "7A6,6A1,W93001,0601060D0AAA0A0,Blood Glucose Test Strips,5,40,38,250,0,0,201804\n"
    "7A3,6A9,W98001,0103050P0AAAAAA,Omeprazole_Cap E/C 20mg,4,2,2,112,0,0,201804\n"
)
ADDRESS = (
    "Period,PracticeId,Locality,Street,Area,Posttown,County,Postcode\n"
    "201804,W93001,,ST. LUKE'S SURGERY,(OFF GWYDDON ROAD),ABERCARN,GWENT,NP11 5GX\n"
    "201804,W98001,,RIVERSIDE SURGERY,,SWANSEA,,SA1 1AA\n"
)


def make_zip(month: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(f"GPData{month.replace('-', '')}.csv", DATA)
        z.writestr("Address.csv", ADDRESS)
        z.writestr("BNF.csv", "BNFChapter\n01\n")
    return buffer.getvalue()


def server(request: httpx.Request) -> httpx.Response:
    if "april-2018" in request.url.path:
        return httpx.Response(200, content=make_zip("2018-04"))
    if request.url.path.endswith("-dec-2025"):  # some months use the short month name
        return httpx.Response(200, content=make_zip("2025-12"))
    if "march-2019" in request.url.path:  # a page that links the wrong month's file
        return httpx.Response(200, content=make_zip("2019-02"))
    return httpx.Response(200, text="<html>Page not found</html>")  # how missing months look


def client():
    return httpx.Client(transport=httpx.MockTransport(server))


def test_months_and_page_addresses():
    assert wales.months_between("2018-11", "2019-02") == [
        "2018-11",
        "2018-12",
        "2019-01",
        "2019-02",
    ]
    assert [u.rsplit("/", 1)[1] for u in wales.page_urls("2018-04")] == [
        "gp-data-extract-april-2018",
        "gp-data-extract-apr-2018",
    ]


def test_falls_back_to_the_short_month_name(tmp_path):
    with client() as c:
        summary = wales.ingest_month(c, "2025-12", raw_dir=tmp_path)
    assert summary["source"].endswith("/gp-data-extract-dec-2025")


def test_refuses_a_zip_for_the_wrong_month(tmp_path):
    with client() as c, pytest.raises(ValueError, match="GPData201902"):
        wales.ingest_month(c, "2019-03", raw_dir=tmp_path)


def test_downloads_filters_and_checks_a_month(tmp_path):
    with client() as c:
        summary = wales.ingest_month(c, "2018-04", raw_dir=tmp_path)
    assert summary["items"] == 42  # every item in the file is in the practices file
    path = tmp_path / "wales_diabetes" / "year_month=2018-04" / "data.parquet"
    rows = (
        duckdb.connect()
        .execute(
            f"SELECT practice_code, bnf_chemical_code, target_presentation, items FROM '{path}' "
            "ORDER BY ALL"
        )
        .fetchall()
    )
    assert rows == [
        ("W93001", "0601022B0", None, "30.0"),
        ("W93001", "0601023AW", "Ozempic_Inj 1mg/0.74ml", "1.0"),
        ("W93001", "0601023AW", "Semaglutide_Inj 1mg/0.74ml", "2.0"),
    ]  # test strips (6.1.6) and omeprazole left out
    register = tmp_path / "wales_register" / "year_month=2018-04" / "data.parquet"
    assert duckdb.connect().execute(
        f"SELECT practice_name, postcode FROM '{register}' WHERE practice_code = 'W93001'"
    ).fetchone() == ("ST. LUKE'S SURGERY", "NP11 5GX")


def test_a_month_without_a_zip_is_not_published(tmp_path):
    with client() as c, pytest.raises(wales.NotPublishedError):
        wales.ingest_month(c, "2018-05", raw_dir=tmp_path)
    assert not (tmp_path / "wales_diabetes").exists()


def test_reads_zips_sent_by_nwssp_from_a_local_folder(tmp_path):
    local = tmp_path / "from_nwssp"
    local.mkdir()
    (local / "GPData201801.zip").write_bytes(make_zip("2018-01"))

    def fail(request):
        raise AssertionError("should not download")

    raw = tmp_path / "raw"
    with httpx.Client(transport=httpx.MockTransport(fail)) as c:
        summary = wales.ingest_month(c, "2018-01", raw_dir=raw, local_dir=local)
        assert wales.ingest_month(c, "2018-01", raw_dir=raw, local_dir=local) is None
    assert summary["source"].startswith("local file GPData201801.zip")
    saved = json.loads((raw / "wales_diabetes" / "year_month=2018-01" / "_source.json").read_text())
    assert len(saved["zip_sha256"]) == 64


def test_detects_each_files_encoding(tmp_path):
    utf8, latin1 = tmp_path / "a.csv", tmp_path / "b.csv"
    utf8.write_bytes("Name\nOrange cr\u00e8me\n".encode())
    latin1.write_bytes("Name\nOrange cr\u00e8me\n".encode("latin-1"))
    assert wales.detect_encoding(utf8) == "utf-8"
    assert wales.detect_encoding(latin1) == "latin-1"
