"""Run the whole dbt project on the sample data (tests/sample_data.py) and check the answers.

This is the end-to-end test: every model is built and every data test runs, as in
`uv run build-warehouse`, but on ~220 made-up prescribers instead of 182 million rows.
"""

import duckdb
import pytest
from dbt.cli.main import dbtRunner
from sample_data import write_sample_raw_data

from semaglutide_spread import warehouse


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    data_dir = write_sample_raw_data(tmp_path_factory.mktemp("data"))
    work_dir = tmp_path_factory.mktemp("dbt")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SEMAGLUTIDE_DATA_DIR", str(data_dir))
        result = dbtRunner().invoke(warehouse.dbt_args("build", ["--quiet"], work_dir=work_dir))
    failed = [
        f"{r.node.name}: {r.message}"
        for r in (result.result or [])
        if r.status not in ("success", "pass")
    ]
    assert result.success, failed or result.exception
    # dbt-duckdb keeps its own connection open in this process, so open it the same way.
    con = duckdb.connect(str(data_dir / "warehouse.duckdb"))
    yield con
    con.close()


def query(con, sql):
    return con.execute(sql).fetchall()


def test_adopters_and_their_adoption_year(built):
    assert query(built, "select npi, adoption_year from adopters order by npi") == [
        ("0000000001", 2018),
        ("0000000002", 2019),
        ("0000000003", 2020),
        ("0000000004", 2020),
        ("0000000006", 2019),
    ]


def test_specialty_and_place_come_from_the_adoption_year(built):
    # B was an internist in Oakland in 2018 and a family doctor in El Cerrito when adopting.
    row = query(
        built,
        "select specialty, county_fips, adoption_brands from adopters where npi = '0000000002'",
    )
    assert row == [("Family Practice", "06013", "Ozempic, Rybelsus")]


def test_every_location_method_is_used(built):
    methods = dict(
        query(
            built,
            "select npi, location_method from int_prescriber_locations "
            "where npi < '1' and year = (select max(year) from int_prescriber_locations "
            "  as l where l.npi = int_prescriber_locations.npi)",
        )
    )
    assert methods["0000000001"] == "zcta_centroid"
    assert methods["0000000003"] == "geocoded_address"
    assert methods["0000000006"] == "zip_geocoded_median"
    assert methods["0000000004"] == "zip3_median"
    assert methods["0000000005"] == "unplaced"


def test_zcta_takes_the_county_with_most_land(built):
    assert query(built, "select county_fips from int_zcta_primary_county where zcta = '94530'") == [
        ("06013",)
    ]


def test_none_specialty_is_unknown(built):
    assert query(
        built,
        "select specialty, specialty_group from int_prescriber_years where npi = '0000000007'",
    ) == [("Unknown", "Organization or unknown")]


def test_adoption_curve(built):
    rows = query(
        built,
        "select year, at_risk, susceptible, new_adopters, adopted_at_risk, cumulative_adopters, "
        "all_prescribers from rpt_adoption_curve order by year",
    )
    assert rows == [
        # 150 background + A, B, C + Z;  +50 non-diabetes prescribers in all_prescribers
        (2018, 154, 154, 1, 1, 1, 204),
        # 150 + A, B, C, E, F; A already adopted; B and F adopt
        (2019, 155, 154, 2, 3, 3, 205),
        # 150 + A, B, C, D, G; F no longer at risk (no diabetes drugs), so 4 of 5 adopters
        (2020, 155, 153, 2, 4, 5, 206),
    ]


def test_night_lights_gif_from_the_built_warehouse(built, tmp_path):
    from PIL import Image

    from semaglutide_spread.viz import night_lights

    gif, png = night_lights.render(built, out_dir=tmp_path, width=320, frames_per_year=2)
    with Image.open(gif) as image:
        assert image.n_frames == 6  # 3 years x 2 frames
    assert png.name == "us_night_lights_2020.png"
