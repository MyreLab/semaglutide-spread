from dbt.cli.main import dbtRunner

from semaglutide_spread import warehouse


def test_dbt_args_point_at_the_repo_project():
    args = warehouse.dbt_args("build", ["--select", "staging"])
    assert args[0] == "build"
    assert args[args.index("--project-dir") + 1] == str(warehouse.DBT_DIR)
    assert args[args.index("--profiles-dir") + 1] == str(warehouse.DBT_DIR)
    assert args[-2:] == ["--select", "staging"]


def test_dbt_project_parses(tmp_path, monkeypatch):
    """The project, its sources, models, seeds and tests are valid dbt. Needs no data."""
    monkeypatch.setenv("SEMAGLUTIDE_DATA_DIR", str(tmp_path))
    args = warehouse.dbt_args("parse", ["--quiet"])
    result = dbtRunner().invoke(args)
    assert result.success, result.exception
