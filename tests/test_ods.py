import json
from urllib.parse import parse_qs, urlparse

import duckdb
import httpx
import pytest

from semaglutide_spread.sources import ods

PRACTICES = [{"OrgId": f"A{81000 + i}", "Name": f"P{i}", "Status": "Active", "PostCode": "LS1 1AA",
              "LastChangeDate": "2024-01-01"} for i in range(5)]  # fmt: skip


def fake_ods(requests):
    def handler(request: httpx.Request) -> httpx.Response:
        params = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        requests.append(params)
        if params.get("Offset") == "0":
            return httpx.Response(406)  # the real API rejects Offset=0
        offset, limit = int(params.get("Offset", 0)), int(params["Limit"])
        body = {"Organisations": PRACTICES[offset : offset + limit]}
        return httpx.Response(200, json=body, headers={"X-Total-Count": str(len(PRACTICES))})

    return handler


def test_pages_through_every_gp_practice(tmp_path):
    requests = []
    with httpx.Client(transport=httpx.MockTransport(fake_ods(requests))) as client:
        assert ods.run(client, raw_dir=tmp_path, page_size=2) == 5
    assert [r.get("Offset") for r in requests] == [None, "2", "4"]
    assert all(r["NonPrimaryRoleId"] == ods.GP_PRACTICE for r in requests)
    path = tmp_path / "ods_gp_practices" / "data.parquet"
    assert duckdb.connect().execute(f"SELECT count(*) FROM '{path}'").fetchone() == (5,)
    assert json.loads((path.parent / "_source.json").read_text())["active"] == 5


def test_fails_if_the_count_does_not_match(tmp_path):
    def short(request):
        return httpx.Response(
            200, json={"Organisations": PRACTICES[:2]}, headers={"X-Total-Count": "3"}
        )

    with httpx.Client(transport=httpx.MockTransport(short)) as client, pytest.raises(RuntimeError):
        ods.run(client, raw_dir=tmp_path, page_size=1000)
