"""Build the DuckDB warehouse with dbt: load seeds, build models, run tests.

This is a thin wrapper around `dbt build` that points dbt at this repo's dbt/ project and
data/ folder, so the command works from anywhere in the repo.

Inputs:  data/raw/ (written by the downloaders), dbt/seeds/*.csv
Output:  data/warehouse.duckdb

Usage:
    uv run build-warehouse                        # everything
    uv run build-warehouse --select staging       # any `dbt build` arguments are passed through
"""

from __future__ import annotations

import os
import sys

from dbt.cli.main import dbtRunner

from semaglutide_spread.paths import DATA_DIR, PROJECT_ROOT

DBT_DIR = PROJECT_ROOT / "dbt"


def dbt_args(command: str, extra: list[str]) -> list[str]:
    return [
        command,
        "--project-dir", str(DBT_DIR),
        "--profiles-dir", str(DBT_DIR),
        "--log-path", str(DBT_DIR / "logs"),
        "--target-path", str(DBT_DIR / "target"),
        *extra,
    ]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    os.environ["SEMAGLUTIDE_DATA_DIR"] = str(DATA_DIR)
    result = dbtRunner().invoke(dbt_args("build", sys.argv[1:] if argv is None else argv))
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
