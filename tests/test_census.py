import io
import json
import zipfile

import duckdb
import httpx
import pytest

from semaglutide_spread.sources import census
from semaglutide_spread.sources.common import SchemaMismatchError

# Shaped like the real gazetteer: tab-separated, with the last column name and every line padded
# with trailing spaces, and ZCTAs that start with zeros (Puerto Rico).
GAZETTEER_TXT = (
    "GEOID\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG" + " " * 40 + "\n"
    "00601\t166659744\t799292\t64.348\t0.309\t18.180555\t-66.749961" + " " * 40 + "\n"
    "94530\t17000000\t100\t6.6\t0.0\t37.9157\t-122.2972" + " " * 40 + "\n"
)

# Shaped like the real relationship file: pipe-separated, starts with a byte-order mark, and
# includes a county part that lies outside every ZCTA (blank ZCTA columns).
RELATIONSHIP_TXT = (
    "﻿OID_ZCTA5_20|GEOID_ZCTA5_20|NAMELSAD_ZCTA5_20|GEOID_COUNTY_20|NAMELSAD_COUNTY_20"
    "|AREALAND_PART\n"
    "|||01003|Baldwin County|339765765\n"
    "2|94530|ZCTA5 94530|06013|Contra Costa County|17000000\n"
)


def _zip(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def _server(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith(".zip"):
        return httpx.Response(200, content=_zip("gazetteer.txt", GAZETTEER_TXT))
    return httpx.Response(200, content=RELATIONSHIP_TXT.encode())


GAZETTEER, RELATIONSHIP = census.REFERENCE_FILES


def _read(path):
    con = duckdb.connect()
    columns = [
        r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()
    ]
    rows = con.execute(f"SELECT * FROM read_parquet('{path}')").fetchall()
    return columns, rows


def test_gazetteer_is_unzipped_and_padding_removed_from_column_names(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(_server)) as client:
        rows = census.ingest_reference_file(client, GAZETTEER, raw_dir=tmp_path)

    out = tmp_path / "census_zcta_gazetteer_2020"
    assert rows == 2
    assert sorted(p.name for p in out.iterdir()) == ["_source.json", "data.parquet"]
    columns, data = _read(out / "data.parquet")
    assert columns[-1] == "INTPTLONG"  # not "INTPTLONG      ..."
    assert data[0][0] == "00601"  # leading zeros kept


def test_relationship_file_keeps_county_parts_outside_any_zcta(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(_server)) as client:
        census.ingest_reference_file(client, RELATIONSHIP, raw_dir=tmp_path)

    columns, data = _read(tmp_path / "census_zcta_county_2020" / "data.parquet")
    assert columns[0] == "OID_ZCTA5_20"  # byte-order mark removed
    assert len(data) == 2
    assert data[0][1] is None  # the raw layer keeps it; staging decides what to do with it


def test_skips_a_file_already_saved_from_the_same_url(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(_server)) as client:
        census.ingest_reference_file(client, RELATIONSHIP, raw_dir=tmp_path)

    def fail(request):
        raise AssertionError("should not download")

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        assert census.ingest_reference_file(client, RELATIONSHIP, raw_dir=tmp_path) is None


def test_records_provenance(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(_server)) as client:
        census.ingest_reference_file(client, GAZETTEER, raw_dir=tmp_path)

    metadata = json.loads((tmp_path / "census_zcta_gazetteer_2020" / "_source.json").read_text())
    assert metadata["url"] == GAZETTEER.url
    assert metadata["rows"] == 2


def test_fails_clearly_when_expected_columns_are_missing(tmp_path):
    def server(request):
        return httpx.Response(200, content=b"SOMETHING|ELSE\n1|2\n")

    with (
        httpx.Client(transport=httpx.MockTransport(server)) as client,
        pytest.raises(SchemaMismatchError, match="GEOID_ZCTA5_20"),
    ):
        census.ingest_reference_file(client, RELATIONSHIP, raw_dir=tmp_path)
    assert list((tmp_path / "census_zcta_county_2020").iterdir()) == []


def test_extract_single_file_rejects_archives_with_several_files(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", "a")
        archive.writestr("b.txt", "b")
    zip_path = tmp_path / "two.zip"
    zip_path.write_bytes(buffer.getvalue())
    with pytest.raises(ValueError, match="one file"):
        census.extract_single_file(zip_path, tmp_path / "out.txt")
