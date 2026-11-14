# Methods

How the [README](../README.md)'s numbers are defined, and what the US vs UK comparison can and can't say. The data sources and limitations are in the README. Every problem found in the source data, and how it was handled, is in [data-notes.md](data-notes.md).

## Definitions

| Term | US | UK |
|---|---|---|
| Glucose-lowering drugs | WHO ATC class A10 | BNF 6.1.1 and 6.1.2 (insulins and antidiabetic drugs) |
| Population at risk | Prescribers with 11+ Part D claims for at least one glucose-lowering drug (semaglutide included) in that year | GP practices with 1+ glucose-lowering item in that month |
| Adoption | First year with 11+ claims for semaglutide, any brand (Ozempic, Rybelsus, Wegovy) | First month whose past 12 months add up to 11+ semaglutide items, any brand (the first month with 1+ item is also kept) |
| Susceptible | In the population at risk and not yet adopted | Same |
| Incidence | New adopters / susceptible, each year | Same, each month |
| Prevalence | Ever adopted / that year's population at risk | Same, each month |

Specialty and location are taken from the year (US) or month (UK) of adoption. In the US, 263 specialty labels are grouped into 13 groups, and each specialty is classed as expected to prescribe diabetes drugs or not, with a reason ([dbt/seeds/specialty_expectations.csv](../dbt/seeds/specialty_expectations.csv)).

### Places

- **US states** come from the county of each prescriber's location that year.
- **US rurality** is the Rural-Urban Commuting Area (RUCA) code of the prescriber's address, as CMS publishes it: codes 1-3 are metropolitan, 4-6 micropolitan, and 7-10 small town and rural.
- **UK areas** are England's nine regions and the other three nations.
- **UK rurality** is the ONS 2021 rural-urban classification of the practice's postcode (urban or rural), for England and Wales only: Scotland and Northern Ireland use different classifications.

## What's being compared

The two countries' data differ in what they count, so the comparison is about the shape and timing of the spread, not its level.

| | US | UK |
|---|---|---|
| Who adopts | An individual prescriber | A GP practice (all of its prescribers) |
| Patients covered | Medicare Part D: mostly people 65 and over | NHS GP patients of all ages |
| Prescriptions | Filled at pharmacies | Dispensed in the community (pharmacies, dispensing practices) |
| Adoption | First year with 11+ claims | First month with 11+ items in the past 12 months |
| Population at risk | 11+ claims for a glucose-lowering drug that year | 1+ glucose-lowering item that month |
| Not in the data | Private insurance, Medicaid, hospitals | Private prescriptions, hospitals, specialist weight-management services |

Shares of prescriptions use US 30-day fills (a 90-day fill counts as 3) and UK items, which have no fixed length. Medicare's patients are older than UK GP patients as a whole, so the levels are indicative, not exact.
