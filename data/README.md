# data/

This folder is empty in git on purpose. The pipeline downloads the source files here and builds everything else from them, so anyone can reproduce the results from scratch.

| Folder | Contents |
|---|---|
| `raw/` | Source files exactly as downloaded, stored as Parquet |
| `output/` | Finished tables, charts and GIFs |
| `warehouse.duckdb` | The DuckDB database that dbt builds |

Sources are listed in the main README.
