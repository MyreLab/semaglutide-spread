"""Where the project keeps its data.

Every script imports its paths from here, so the folder layout is defined in one place.
Nothing under data/ is committed to git; it is rebuilt by running the pipeline.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"  # files exactly as downloaded (stored as Parquet)
OUTPUT_DIR = DATA_DIR / "output"  # finished tables, charts and GIFs
WAREHOUSE_PATH = DATA_DIR / "warehouse.duckdb"  # the DuckDB database dbt builds into
