# semaglutide-spread

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

## Run it yourself

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/MyreLab/semaglutide-spread.git
cd semaglutide-spread
uv sync                      # create the environment from uv.lock
uv run pre-commit install    # turn on the checks that run on every commit
uv run pytest                # run the tests
```

## Project layout

```
src/semaglutide_spread/   Python code: downloads, maps, animations
tests/                    automated tests
data/                     downloaded and built data (not committed; see data/README.md)
```

## Roadmap

- [ ] **M1: US adoption.** Part D 2018-2024, population at risk, first adoption, US night-lights GIF
- [ ] **M2: England and Scotland.** Monthly GP practice data, same measures, UK night-lights GIF
- [ ] **M3: Compare.** Epidemic curves, specialty breakdown, US vs UK
- [ ] **M4: Agents.** Claude-powered helpers for drug-name matching, data quality triage and questions

## License

MIT
