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
    assert query(built, "select npi, adoption_year from us_adopters order by npi") == [
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
        "select specialty, county_fips, adoption_brands from us_adopters where npi = '0000000002'",
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
        "all_prescribers from rpt_us_adoption_curve order by year",
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


def test_uk_night_lights_gif_from_the_built_warehouse(built, tmp_path):
    from PIL import Image

    from semaglutide_spread.viz import night_lights

    gif, png = night_lights.render(built, night_lights.UK, out_dir=tmp_path, width=320)
    first, last = built.execute(
        "select min(first), min(last) from (select min(month) as first, max(month) as last "
        "from uk_population_at_risk group by nation)"
    ).fetchone()
    months = (last.year - first.year) * 12 + last.month - first.month + 1
    with Image.open(gif) as image:
        assert image.n_frames == months  # one frame a month, to the last month all nations have
    assert png.name == f"uk_night_lights_{last:%Y-%m}.png"


def test_adoption_curve_chart_from_the_built_warehouse(built, tmp_path):
    from semaglutide_spread.viz import adoption_curve

    paths = adoption_curve.render(built, out_dir=tmp_path)
    assert sorted(p.name for p in paths) == [
        "us_adoption_curve_dark.png",
        "us_adoption_curve_light.png",
    ]


def test_england_adopters_and_both_adoption_months(built):
    rows = query(
        built,
        "select practice_code, adoption_month::varchar, matched_adoption_month::varchar, "
        "adoption_brands, postcode_status from uk_adopters where nation = 'England' order by 1",
    )
    assert rows == [
        ("A81001", "2019-02-01", "2019-05-01", "Ozempic", "current"),
        ("B82002", "2019-04-01", None, "Rybelsus", "current"),
        ("Y01008", "2019-06-01", None, "Generic name", "terminated"),
    ]


def test_england_adoption_curve(built):
    rows = query(
        built,
        "select month::varchar, at_risk, susceptible, new_adopters, adopted_at_risk, "
        "matched_new_adopters, matched_adopted_at_risk from rpt_uk_adoption_curve "
        "where nation = 'England' order by 1",
    )
    assert rows == [
        # A, B, Y and D at risk; C prescribes no diabetes drugs; unidentified left out
        ("2019-01-01", 4, 4, 0, 0, 0, 0),
        ("2019-02-01", 4, 4, 1, 1, 0, 0),  # A adopts
        ("2019-03-01", 4, 3, 0, 1, 0, 0),
        ("2019-04-01", 3, 2, 1, 2, 0, 0),  # D has closed; B adopts
        ("2019-05-01", 3, 1, 0, 2, 1, 1),  # A's past 12 months reach 11 items
        ("2019-06-01", 3, 1, 1, 3, 0, 1),  # Y adopts
    ]


def test_scotland_keeps_only_registered_gp_practices(built):
    assert query(
        built,
        "select practice_code, adoption_month::varchar, matched_adoption_month, adoption_brands, "
        "nhs_area_name, list_size from uk_adopters where nation = 'Scotland'",
    ) == [("10002", "2019-03-01", None, "Ozempic", "NHS Tayside", 8643)]
    rows = query(
        built,
        "select month::varchar, at_risk, new_adopters, adopted_at_risk from rpt_uk_adoption_curve "
        "where nation = 'Scotland' order by 1",
    )
    assert [r[1] for r in rows] == [2] * 6  # 99996 is never counted
    assert [r[2] for r in rows] == [0, 0, 1, 0, 0, 0]


def test_northern_ireland_keeps_only_listed_gp_practices(built):
    assert query(
        built,
        "select practice_code, adoption_month::varchar, adoption_brands, nhs_area_name, list_size "
        "from uk_adopters where nation = 'Northern Ireland'",
    ) == [("1", "2019-05-01", "Generic name", "Belfast", 6783)]
    rows = query(
        built,
        "select at_risk from rpt_uk_adoption_curve "
        "where nation = 'Northern Ireland' order by month",
    )
    assert rows == [(2,)] * 6  # code 999 is not a GP practice


def test_wales_keeps_only_gp_practices(built):
    assert query(
        built,
        "select practice_code, adoption_month::varchar, adoption_brands, nhs_area_name "
        "from uk_adopters where nation = 'Wales'",
    ) == [("W93001", "2019-03-01", "Ozempic", "Aneurin Bevan University Health Board")]
    rows = query(
        built,
        "select at_risk from rpt_uk_adoption_curve where nation = 'Wales' order by month",
    )
    assert rows == [(2,)] * 6  # the health trust W00024 is not a GP practice
