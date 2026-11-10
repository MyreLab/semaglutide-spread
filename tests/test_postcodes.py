import json

import duckdb
import httpx
import pytest

from semaglutide_spread.sources import postcodes

CURRENT = {
    "LS1 1AA": {
        "latitude": 53.79,
        "longitude": -1.54,
        "country": "England",
        "region": "Yorkshire and The Humber",
        "icb": "NHS West Yorkshire Integrated Care Board",
        "admin_district": "Leeds",
        "ruc21": "Urban: Nearer to a major town or city",
        "codes": {"icb": "E54000054", "admin_district": "E08000035", "lsoa21": "E01033010"},
    }
}
TERMINATED = {
    "E1W 1UU": {
        "latitude": 51.508,
        "longitude": -0.0737,
        "year_terminated": 2015,
        "month_terminated": 2,
    }
}


@pytest.fixture
def raw_dir(tmp_path):
    con = duckdb.connect()
    for month, values in (
        ("2020-01", "('ls11aa'), ('E1W 1UU'), ('-')"),
        ("2020-02", "('ZZ9 9ZZ')"),
    ):
        out = tmp_path / "epd_practices" / f"year_month={month}"
        out.mkdir(parents=True)
        con.execute(
            f"COPY (SELECT * FROM (VALUES {values}) AS t(postcode)) TO '{out / 'data.parquet'}'"
        )
    return tmp_path


class FakePostcodesIo:
    def __init__(self):
        self.bulk_sizes: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            asked = json.loads(request.content)["postcodes"]
            self.bulk_sizes.append(len(asked))
            result = [{"query": pc, "result": CURRENT.get(pc)} for pc in asked]
            return httpx.Response(200, json={"status": 200, "result": result})
        postcode = request.url.path.rsplit("/", 1)[-1]
        if postcode in TERMINATED:
            return httpx.Response(200, json={"status": 200, "result": TERMINATED[postcode]})
        return httpx.Response(404, json={"status": 404, "error": "Terminated postcode not found"})


def test_normalises_postcodes():
    assert postcodes.normalise(" ls11aa ") == "LS1 1AA"
    assert postcodes.normalise("EC1A 1BB") == "EC1A 1BB"
    assert postcodes.normalise("-") is None
    assert postcodes.normalise(None) is None


def test_collects_distinct_postcodes_from_every_month(raw_dir):
    assert postcodes.find_postcodes(raw_dir) == ["E1W 1UU", "LS1 1AA", "ZZ9 9ZZ"]


def test_looks_up_current_then_terminated(raw_dir):
    fake = FakePostcodesIo()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        results = postcodes.run(client, raw_dir=raw_dir, batch_size=2)

    assert fake.bulk_sizes == [2, 1]
    leeds = results["LS1 1AA"]
    assert (leeds.status, leeds.icb_code, leeds.latitude) == ("current", "E54000054", "53.79")
    assert results["E1W 1UU"].status == "terminated"
    assert results["E1W 1UU"].terminated == "2015-02"
    assert results["ZZ9 9ZZ"].status == "not_found"

    summary = json.loads((raw_dir / "postcodes" / "_source.json").read_text())
    assert summary["by_status"] == {"current": 1, "terminated": 1, "not_found": 1}


def test_does_not_look_up_a_postcode_twice(raw_dir):
    with httpx.Client(transport=httpx.MockTransport(FakePostcodesIo())) as client:
        postcodes.run(client, raw_dir=raw_dir)

    def fail(request):
        raise AssertionError("should not call postcodes.io")

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        results = postcodes.run(client, raw_dir=raw_dir)
    assert results["LS1 1AA"].region == "Yorkshire and The Humber"  # read back from the cache
