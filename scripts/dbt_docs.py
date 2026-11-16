"""Build the dbt docs site on the sample data, as one static HTML page.

The docs need a built warehouse to read column types from, and CI doesn't have the real
data, so this builds the dbt project on the made-up sample data the tests use. Lineage,
descriptions and tests are the same as on the real data.

Usage:
    uv run python scripts/dbt_docs.py site/      # writes site/index.html
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from dbt.cli.main import dbtRunner

from semaglutide_spread.warehouse import dbt_args

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from sample_data import write_sample_raw_data


def main(out_dir: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        os.environ["SEMAGLUTIDE_DATA_DIR"] = str(write_sample_raw_data(tmp / "data"))
        dbt = dbtRunner()
        if not dbt.invoke(dbt_args("build", ["--quiet"], work_dir=tmp)).success:
            return 1
        # `docs generate` is a subcommand, so "docs" goes in front of dbt_args' arguments.
        if not dbt.invoke(["docs", *dbt_args("generate", ["--static"], work_dir=tmp)]).success:
            return 1
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(tmp / "target" / "static_index.html", out_dir / "index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "site")))
