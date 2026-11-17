"""int_epd_practice_months is incremental: adding a month and building again must give the
same table as rebuilding from scratch."""

import shutil

import duckdb
import pytest
from dbt.cli.main import dbtRunner
from sample_data import write_sample_raw_data

from semaglutide_spread import warehouse

MODEL = "int_epd_practice_months"


def dbt(work_dir, *args):
    result = dbtRunner().invoke(warehouse.dbt_args("run", ["--quiet", *args], work_dir=work_dir))
    assert result.success


def rows(data_dir):
    # dbt-duckdb keeps its own connection open in this process, so open it the same way.
    with duckdb.connect(str(data_dir / "warehouse.duckdb")) as con:
        return sorted(con.execute(f"select * from {MODEL}").fetchall(), key=repr)


@pytest.fixture
def builds(tmp_path, monkeypatch):
    data_dir = write_sample_raw_data(tmp_path / "data")
    monkeypatch.setenv("SEMAGLUTIDE_DATA_DIR", str(data_dir))
    raw = data_dir / "raw"

    # Hold back the last month of English data, as if it hadn't been published yet.
    held = tmp_path / "held"
    months = sorted(p.name for p in (raw / "epd_practices").iterdir())
    latest = months[-1]
    for table in ["epd_diabetes", "epd_practices"]:
        shutil.move(raw / table / latest, held / table / latest)

    dbt(tmp_path, "--select", f"+{MODEL}", "--exclude", "resource_type:test")
    before = rows(data_dir)

    # The month is published: an incremental build adds it.
    for table in ["epd_diabetes", "epd_practices"]:
        shutil.move(held / table / latest, raw / table / latest)
    dbt(tmp_path, "--select", MODEL)
    incremental = rows(data_dir)

    dbt(tmp_path, "--select", MODEL, "--full-refresh")
    full = rows(data_dir)
    return before, incremental, full, latest


def test_the_incremental_build_adds_the_new_month(builds):
    before, incremental, _, latest = builds
    month = latest.removeprefix("year_month=")
    assert not any(str(row[0]).startswith(month) for row in before)
    assert any(str(row[0]).startswith(month) for row in incremental)


def test_incremental_and_full_refresh_give_the_same_table(builds):
    _, incremental, full, _ = builds
    assert incremental == full
