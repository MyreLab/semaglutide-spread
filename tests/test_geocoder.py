import csv
import io
import json
import re

import duckdb
import httpx
import pytest

from semaglutide_spread.sources import geocoder
from semaglutide_spread.sources.geocoder import Address

# --- Fake raw data -------------------------------------------------------------------------

PROVIDERS = [
    # (NPI, St1, St2, City, State, Zip5, Country)
    ("1", "100 Main St", None, "Oakland", "CA", "94612", "US"),  # ZIP is a ZCTA: not geocoded
    ("2", "9500 Euclid Ave", None, "Cleveland", "OH", "44195", "US"),  # unique ZIP: geocode
    ("3", "Dept Of Medicine", "1 Medical Center Dr", "Baltimore", "MD", "21287", "US"),  # line 2
    ("4", "Clinic Annex", None, "Nashville", "TN", "37232", "US"),  # no match
    ("5", "Unit 35610", None, "Fpo", "AP", "96373", "US"),  # military: left out
    ("6", "1 Yonge St", None, "Toronto", "ZZ", None, "CA"),  # foreign: left out
]
ZCTAS = ["94612"]


@pytest.fixture
def raw_dir(tmp_path):
    con = duckdb.connect()
    for year in (2023, 2024):
        out = tmp_path / "partd_by_provider" / f"year={year}"
        out.mkdir(parents=True)
        values = ", ".join(
            "(" + ", ".join("NULL" if v is None else f"'{v}'" for v in row) + ")"
            for row in PROVIDERS
        )
        con.execute(
            f"""COPY (SELECT * FROM (VALUES {values}) AS t(Prscrbr_NPI, Prscrbr_St1, Prscrbr_St2,
                Prscrbr_City, Prscrbr_State_Abrvtn, Prscrbr_Zip5, Prscrbr_Cntry))
                TO '{out / "data.parquet"}' (FORMAT parquet)"""
        )
    gaz = tmp_path / "census_zcta_gazetteer_2020"
    gaz.mkdir()
    values = ", ".join(f"('{z}')" for z in ZCTAS)
    target = gaz / "data.parquet"
    con.execute(
        f"COPY (SELECT * FROM (VALUES {values}) AS t(GEOID)) TO '{target}' (FORMAT parquet)"
    )
    return tmp_path


# --- Fake geocoder -------------------------------------------------------------------------


def _sent_rows(request: httpx.Request) -> list[list[str]]:
    """Pull the address rows out of the multipart upload."""
    text = request.content.decode()
    return [
        row
        for row in csv.reader(io.StringIO(text))
        if row and re.fullmatch(r"[0-9a-f]{32}", row[0])
    ]


class FakeGeocoder:
    """Matches streets that look like real street addresses; everything else is No_Match."""

    def __init__(self):
        self.requests: list[list[list[str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        rows = _sent_rows(request)
        self.requests.append(rows)
        out = io.StringIO()
        writer = csv.writer(out, quoting=csv.QUOTE_ALL)
        for address_id, street, city, state, zip5 in rows:
            given = f"{street}, {city}, {state}, {zip5}"
            if re.match(r"\d+ \w+", street):
                writer.writerow(
                    [
                        address_id,
                        given,
                        "Match",
                        "Exact",
                        given.upper(),
                        "-81.62,41.50",
                        "123",
                        "L",
                        "39",
                        "035",
                        "197200",
                        "1012",
                    ]
                )
            else:
                writer.writerow([address_id, given, "No_Match"])
        return httpx.Response(200, text=out.getvalue())


# --- Tests ---------------------------------------------------------------------------------


def test_finds_only_us_addresses_whose_zip_is_not_a_zcta(raw_dir):
    addresses = geocoder.find_unplaced_addresses(raw_dir)
    assert sorted(a.zip5 for a in addresses) == [
        "21287",
        "37232",
        "44195",
    ]  # once each, not per year


def test_address_id_ignores_case_and_padding():
    a = Address("9500 Euclid Ave", None, "Cleveland", "OH", "44195")
    b = Address(" 9500 EUCLID AVE ", None, "cleveland", "OH", "44195")
    assert a.address_id == b.address_id


def test_parses_matched_and_unmatched_rows():
    text = (
        '"a","1 X St, Y, OH, 44195","Match","Non_Exact","1 X ST, Y, OH, 44106",'
        '"-81.6,41.5","9","L","39","035","197200","1012"\n'
        '"b","Dept Of X, Y, OH, 44195","No_Match"\n'
        '"c","2 X St, Y, OH, 44195","Tie"\n'
    )
    rows = geocoder.parse_batch_response(text)
    address = Address("1 X St", None, "Y", "OH", "44195")
    match = geocoder.to_result(address, rows["a"], "st1", "now")
    assert (match.longitude, match.latitude) == ("-81.6", "41.5")
    assert match.county_fips == "39035"
    assert match.match_type == "Non_Exact"
    assert geocoder.to_result(address, rows["b"], "st1", "now").status == "No_Match"
    assert geocoder.to_result(address, rows["c"], "st1", "now").status == "Tie"
    assert geocoder.to_result(address, None, "st1", "now").status == "No_Match"


def test_tries_second_line_only_for_unmatched_addresses_that_have_one(raw_dir):
    fake = FakeGeocoder()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        results = geocoder.run(client, raw_dir=raw_dir)

    by_zip = {r.zip5: r for r in results.values()}
    assert by_zip["44195"].status == "Match" and by_zip["44195"].matched_line == "st1"
    assert by_zip["21287"].status == "Match" and by_zip["21287"].matched_line == "st2"
    assert by_zip["37232"].status == "No_Match"
    assert len(fake.requests) == 2  # line 1 for all three, then line 2 for the Baltimore one
    assert [row[1] for row in fake.requests[1]] == ["1 Medical Center Dr"]


def test_splits_into_batches(raw_dir):
    fake = FakeGeocoder()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        geocoder.run(client, raw_dir=raw_dir, batch_size=2)
    assert [len(r) for r in fake.requests] == [2, 1, 1]  # 3 addresses on line 1, then 1 on line 2


def test_saves_cache_and_does_not_resend_addresses(raw_dir):
    with httpx.Client(transport=httpx.MockTransport(FakeGeocoder())) as client:
        geocoder.run(client, raw_dir=raw_dir)

    out = raw_dir / "census_geocoder"
    summary = json.loads((out / "_source.json").read_text())
    assert summary["addresses"] == 3
    assert summary["by_status"] == {"Match": 2, "No_Match": 1}
    assert summary["matched_on_line"] == {"st1": 1, "st2": 1}

    def fail(request):
        raise AssertionError("should not call the geocoder")

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        results = geocoder.run(client, raw_dir=raw_dir)
    assert len(results) == 3


def test_cached_values_round_trip_as_text(raw_dir):
    with httpx.Client(transport=httpx.MockTransport(FakeGeocoder())) as client:
        geocoder.run(client, raw_dir=raw_dir)
    cache = geocoder.load_cache(raw_dir / "census_geocoder" / "data.parquet")
    match = next(r for r in cache.values() if r.zip5 == "44195")
    assert match.county_fips == "39035"  # leading digits kept as text
    unmatched = next(r for r in cache.values() if r.zip5 == "37232")
    assert unmatched.latitude is None
