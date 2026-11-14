# Methods

How the [README](../README.md)'s numbers are defined, what the data cover, and what they can't say. Every problem found in the source data, and how it was handled, is in [data-notes.md](data-notes.md).

## Data

| Country | Source | Unit | Time step |
|---|---|---|---|
| US | Medicare Part D Prescribers by Provider and Drug (CMS) | Individual prescriber | Year |
| England | English Prescribing Dataset (NHS Business Services Authority) | GP practice | Month |
| Scotland | Prescriptions in the Community (Public Health Scotland) | GP practice | Month |
| Northern Ireland | GP Prescribing Data (Business Services Organisation, via OpenDataNI) | GP practice | Month |
| Wales | GP practice prescribing (NHS Wales Shared Services Partnership) | GP practice | Month |

US prescribers are placed on the map from their practice ZIP code, using Census ZIP Code Tabulation Areas and, where a ZIP code has none, the Census geocoder. UK practices are placed from their postcode, using [postcodes.io](https://postcodes.io) (built on the ONS Postcode Directory). Only GP practices are kept: in England and Wales, codes the NHS Organisation Data Service lists as GP practices; in Scotland and Northern Ireland, practices in each nation's practice register.

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

## Limitations

US:

- **Medicare only.** Part D covers people 65 and over and some younger people with disabilities. Most plans don't cover drugs for weight loss alone, so most weight-loss prescribing isn't in this data.
- **Yearly data.** Adoption is known to the year, not the month.
- **The 11-claim rule.** CMS hides any prescriber x drug x year with fewer than 11 claims. Adoption is therefore the first year a prescriber becomes *visible*, which can be later than their first prescription.
- **Claims, not patients.** A claim is one fill, refills included, so one patient on a monthly prescription is enough to reach 11 claims. Most adopters had fewer than 11 patients on semaglutide in their adoption year.
- **No Medicaid.** Medicaid publishes prescriptions by state and drug, but not by prescriber, so it can't be added.
- **No diagnoses.** The data can't say why semaglutide was prescribed.

UK:

- **Practices, not prescribers.** One adopting GP turns on the whole practice.
- **NHS GP prescribing only.** Private prescriptions, including online weight-loss services, and specialist NHS weight-management services aren't in the data.
- **No patient ages or diagnoses.** The data can't be limited to older patients, or say why semaglutide was prescribed.
- **Scotland's months are when the pharmacy was paid,** usually a month after dispensing; the other nations' are when it was dispensed.
