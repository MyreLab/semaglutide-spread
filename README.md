# semaglutide-spread

[![CI](https://github.com/MyreLab/semaglutide-spread/actions/workflows/ci.yml/badge.svg)](https://github.com/MyreLab/semaglutide-spread/actions/workflows/ci.yml)

How semaglutide (Ozempic) spread through prescribers in the US and the UK, tracked like an epidemic.

> **Status:** work in progress. See the [milestones](https://github.com/MyreLab/semaglutide-spread/milestones).

## The question

Epidemiologists track how a disease moves through a population: who catches it first, how fast it spreads, and where. This project asks the same questions about a new drug. Each prescriber (US) or GP practice (UK) "catches" semaglutide the first time they prescribe it in meaningful volume, and the project follows that spread over time and across the map.

## Data

| Country | Source | Unit | Time step |
|---|---|---|---|
| US | Medicare Part D Prescribers by Provider and Drug (CMS) | Individual prescriber | Year |
| England | English Prescribing Dataset (NHS Business Services Authority) | GP practice | Month |
| Scotland | Prescriptions in the Community (Public Health Scotland) | GP practice | Month |
| Northern Ireland | GP Prescribing Data (Business Services Organisation, via OpenDataNI) | GP practice | Month |
| Wales | GP practice prescribing (NHS Wales Shared Services Partnership) | GP practice | Month |

## Run it yourself

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/MyreLab/semaglutide-spread.git
cd semaglutide-spread
uv sync                      # create the environment from uv.lock
uv run pre-commit install    # turn on the checks that run on every commit
uv run pytest                # run the tests, including a dbt build on made-up sample data
```

Download the data:

```bash
uv run partd-download        # Medicare Part D, 2018 to the latest year CMS has published
uv run census-download       # Census ZIP-area (ZCTA) reference files, ~2 MB
uv run geocode-addresses     # place prescribers whose ZIP code has no ZIP area (~4 minutes)
```

Build the warehouse (types, tests, and later the analysis tables):

```bash
uv run build-warehouse       # dbt build: ~3 minutes, writes data/warehouse.duckdb
```

Draw the maps:

```bash
uv run night-lights          # docs/images/us_night_lights.gif and a still of the last year (~20 s)
```

The Part D download is about 33 GB of CSVs from data.cms.gov, one file at a time, and stores them as about 4.6 GB of Parquet in `data/raw/`. Allow about 10 GB of free disk space while it runs. Re-running only fetches years that are new or that CMS has re-published.

Every pull request runs the same checks and tests on GitHub Actions; the dbt models are tested end to end on a small made-up dataset (`tests/sample_data.py`) with known answers.

Problems found in the source data, and how they are handled, are documented in [docs/data-notes.md](docs/data-notes.md).

## Project layout

```
src/semaglutide_spread/   Python code: downloads, geocoding, maps, animations
dbt/                      SQL models that clean and combine the data, with data tests
tests/                    automated tests
docs/                     notes on the data, and the images shown in this README
data/                     downloaded and built data (not committed; see data/README.md)
```

## Roadmap

- [ ] **M1: US adoption.** Part D 2018-2024, population at risk, first adoption, US night-lights GIF
- [ ] **M2: UK.** Monthly GP practice data for England, Scotland, Northern Ireland and Wales, same measures, UK night-lights GIF
- [ ] **M3: Compare.** Epidemic curves, specialty breakdown, US vs UK
- [ ] **M4: Agents.** Claude-powered helpers for drug-name matching, data quality triage and questions

## License

MIT
