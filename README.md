# semaglutide-spread

[![Continuous integration tests](https://github.com/MyreLab/semaglutide-spread/actions/workflows/ci.yml/badge.svg)](https://github.com/MyreLab/semaglutide-spread/actions/workflows/ci.yml)

A data pipeline that tracks how semaglutide (Ozempic, Rybelsus, Wegovy) spread through prescribers in the US and the UK, built on public prescribing data with Python, dbt, DuckDB and Dagster.

<img src="docs/images/us_uk_night_lights.gif" alt="Night-lights maps of the US and the UK side by side on one clock, 2018 to 2026: each US Medicare prescriber and each UK GP practice lights up when it first reaches 11+ semaglutide prescriptions in a year">

*Each light is a US Medicare Part D prescriber (left) or a UK GP practice (right), switched on when it first reaches 11+ semaglutide prescriptions in a year.*

## The question

Epidemiologists track how a disease moves through a population: who catches it first, how fast it spreads, and where. This project asks the same questions about semaglutide, as Coleman, Katz and Menzel did for a new antibiotic in 1950s Illinois (*Medical Innovation: A Diffusion Study*, 1966). Each prescriber (US) or GP practice (UK) "catches" semaglutide the first time they prescribe it in meaningful volume: 11+ prescriptions in a year.

## How it's built

```mermaid
flowchart LR
    A[CMS, Census, NHS<br>downloads] -->|Python, httpx| B[Raw Parquet<br>data/raw]
    B -->|dbt + DuckDB| C[Staging<br>typed, cleaned]
    C --> D[Intermediate<br>locations, specialties]
    D --> E[Marts<br>population at risk, adopters]
    E --> F[Reports<br>adoption curves, places,<br>shares, specialties]
    E -->|numpy, Pillow| G[Night-lights GIFs]
    F -->|matplotlib| H[Charts]
```

- **Ingestion (Python).** One downloader per source. Downloads are resumable and retried, columns are checked against what the publisher documents, and row counts or totals are checked before anything is saved. Everything lands as text-only Parquet, partitioned by year or month, so column types are decided in one place (dbt) instead of guessed on load.
- **Transformation (dbt on DuckDB).** Staging models type and clean each source, intermediate models handle locations and specialties, and marts hold the population at risk and the adopters. Hand-maintained lookups, like the list of diabetes drugs and the specialty groups, are dbt seeds with a reason on each row. Browse the models, tests and lineage in the [dbt docs](https://myrelab.github.io/semaglutide-spread/).
- **Orchestration (Dagster).** The downloads, dbt models and charts are one asset graph, with each dbt test as a check on its table. I picked Dagster over Airflow because its assets map one-to-one onto dbt models, so the lineage runs from the source APIs to the charts. A monthly schedule reruns it; the downloaders skip anything unchanged.
- **Testing.** dbt tests run on the full data at every build, including checks that counts reconcile between layers. A pytest suite also builds the whole warehouse on a small made-up dataset with answers worked out in advance, and runs the Dagster graph on it with the downloads stubbed out. The marts and reports have enforced contracts, so a renamed column or changed type fails the build.
- **CI.** GitHub Actions runs formatting, lint and all tests on every pull request; `main` only changes through pull requests that pass.
- **Outputs.** Charts in matplotlib; the maps are drawn with numpy and Pillow.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/dagster_lineage_dark.png">
  <img alt="One chart's lineage in Dagster, from the raw downloads to the chart" src="docs/images/dagster_lineage_light.png">
</picture>

*One chart's lineage in Dagster, from raw downloads to chart.*

| | US | UK |
|---|---|---|
| Rows ingested | 182M (prescriber x drug x year) | 21.6M (practice x drug x month) |
| Prescriptions those rows summarise | 9.4B claims | 12.2B items |
| Prescribers | 1.54M individual prescribers | 9,134 GP practices |
| Placed on the map | 99.97% | 99.6% |
| Storage | 33 GB of CSV, 4.6 GB as Parquet | 0.3 GB of Parquet (filtered on download) |

## What I found

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/us_uk_comparison_dark.png">
  <img alt="Two charts. Left: the share that have adopted against years since the first adopters; UK GP practices rise steeply to 99%, US prescribers rise steadily to 60% after seven years. Right: semaglutide's share of diabetes prescriptions by year; the US and UK track each other until 2021, then the US rises to 7.4% in 2024 while the UK levels off near 2.4%." src="docs/images/us_uk_comparison_light.png">
</picture>

- **The UK took it up faster.** Half of UK GP practices had adopted semaglutide 18 months after the first ones did. US prescribers took six years to get close (48%). Some of that gap is the unit: a UK practice counts as adopting as soon as any one of its GPs does.
- **But the US uses more of it.** In 2024, semaglutide was 7.4% of Medicare's diabetes prescriptions and 2.5% of the UK's.
- **Specialists first, then primary care.** Endocrinologists were 2% of US diabetes prescribers in 2018 but 31% of that year's adopters. Primary care doctors and nurse practitioners carried it from there.
- **Rural US went further; London came last.** By 2024, 75% of US prescribers in small towns and rural areas had adopted, against 57% in metropolitan areas. In the UK, Wales led and London trailed throughout.
- **And some unexpected prescribers.** 1,294 US adopters (0.6%) came from specialties that rarely manage diabetes, mostly pulmonology, psychiatry and rheumatology, plus 42 naturopaths and 7 dentists.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/us_unexpected_specialties_dark.png">
  <img alt="Horizontal bar chart of the 1,294 US adopters from specialties that rarely manage diabetes, led by pulmonary disease (228), psychiatry (135), rheumatology (83), anesthesiology (71) and registered nurses (71), with 35 smaller specialties grouped as other (170)." src="docs/images/us_unexpected_specialties_light.png">
</picture>

## Problems I ran into

Most of the work was in the data, not the code. A few problems that changed the design:

- **ZIP codes that aren't places.** About 2% of Medicare's ZIP codes have no Census ZIP area to map to; they mostly belong to large medical centres with their own ZIP code. I geocoded those 12,391 addresses and filled the failures from nearby matches, which puts 99.97% of prescriber-years on the map. The build fails if any year drops below 99%.
- **A column that changed meaning.** In March 2025, England's prescribing data started putting drug names in the column that used to hold drug codes. Read the old way, it returns zero diabetes prescriptions and no error. The downloader now checks each month's columns against the layouts it knows and stops on anything new.
- **A Census file from the wrong decade.** The file labelled "2020 Gazetteer" lists the 2010 ZIP areas. A dbt test comparing it with another Census file caught the mismatch (851 ZIP areas in one but not the other), and the downloader now checks the row count the Census Bureau publishes.
- **Searching for diabetes drugs finds the wrong things.** A text search of Medicare's drug names also returns insulin syringes and metolazone, a diuretic. The drug list is now a dbt seed based on the WHO's diabetes drug group, and a test fails if Medicare publishes a new name nobody has classified.
- **Paging an API without losing rows.** England's and Scotland's portals return 32,000 rows per request, and without a fixed sort order pages can overlap or skip rows. Every download uses a fixed order and is checked against a separate total before it's saved.

## Working with Claude Code

I built this with Claude Code as a pair programmer. I chose the questions and the data sources, laid out the architecture I wanted, and made the analytical decisions. I reviewed every pull request before merging. Claude was confidently wrong more than once: it told me the Welsh data only went back to 2024 when the archive went back to 2018, and its first grouping of medical specialties didn't match who actually manages diabetes, so I reclassified them. The tests were my other safeguard: if a change makes the pipeline give the wrong answers on the test dataset, CI fails and it can't be merged.

## Run it yourself

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/MyreLab/semaglutide-spread.git
cd semaglutide-spread
uv sync                      # create the environment from uv.lock
uv run pre-commit install    # turn on the checks that run on every commit
uv run pytest                # run the tests, including a dbt build on made-up sample data
```

Then run the whole pipeline with Dagster:

```bash
uv run dagster dev           # UI at http://localhost:3000; click "Materialize all"
# or, without the UI:
uv run dagster job execute -m semaglutide_spread.definitions -j refresh
```

## Data sources

| Country | Source | Unit | Time step |
|---|---|---|---|
| US | [Medicare Part D Prescribers by Provider and Drug](https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/medicare-part-d-prescribers-by-provider-and-drug) (CMS) | Individual prescriber | Year |
| England | [English Prescribing Dataset](https://opendata.nhsbsa.net/dataset/english-prescribing-dataset-epd-with-snomed-code) (NHS Business Services Authority) | GP practice | Month |
| Scotland | [Prescriptions in the Community](https://www.opendata.nhs.scot/dataset/prescriptions-in-the-community) (Public Health Scotland) | GP practice | Month |
| Northern Ireland | [GP Prescribing Data](https://www.opendatani.gov.uk/dataset/gp-prescribing-data) (Business Services Organisation, via OpenDataNI) | GP practice | Month |
| Wales | [GP practice prescribing](https://nwssp.nhs.wales/ourservices/primary-care-services/general-information/data-and-publications/prescribing-data-extracts/general-practice-prescribing-data-extract/) (NHS Wales Shared Services Partnership) | GP practice | Month |

Limitations and definitions are in [docs/methods.md](docs/methods.md), and year-by-year results for each country are in [docs/results.md](docs/results.md).

## Credits and license

Built by Myrela Bauman | [@MyreLab](https://github.com/MyreLab). Released under the [MIT license](LICENSE).
