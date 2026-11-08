# Data notes

Problems found in the source data, what they look like, and how this project handles them. Each entry has a real example and the effect on the results. Figures are for Medicare Part D 2018-2024 unless stated.

## Medicare Part D (CMS)

### Rows are aggregates, and small counts are removed
Each row of "by Provider and Drug" is one prescriber x one drug (brand) x one year. Rows with fewer than 11 claims are not published.

- **Effect on "adoption":** suppression applies to each prescriber x brand row separately. A prescriber with 6 Ozempic and 6 Rybelsus claims (12 semaglutide claims) does not appear at all. Adoption is therefore defined as *the first year with 11+ claims for at least one semaglutide brand*.
- **Wording:** the 181.8 million rows represent 9.38 billion claims (sum of `Tot_Clms`). The project never handles individual claims.

### Two further suppression markers in the 65-and-over columns
`GE65_Sprsn_Flag` and `GE65_Bene_Sprsn_Flag` replace values with `*` or `#` (81.0 million rows carry one of them), so these columns are not numeric as published. This project does not use the 65-and-over breakdown.

### Names and places already corrupted at the source
142 rows contain the replacement character "�" where an accented letter was, all in Puerto Rico:

| Last name | First name | City |
|---|---|---|
| Pe�a-Alvarado | Zaida | Orocovis |
| Rivera-Monta�ez | Rehuel | Caguas |
| Maldonado | Concepci�n | San Juan |
| Natal | Daniel | Mayag�ez |

The original letters cannot be recovered. Names are not used in the analysis; city is not used for location (ZIP code is).

### One line that is not valid text
The 2020 "by Provider and Drug" file (3.6 GB) has one line whose bytes are not valid UTF-8 (the Mayagüez row above), which makes the file unreadable as published. The downloader replaces only the invalid bytes with "�" and records `repaired_lines: 1` in that year's `_source.json`. No rows are dropped.

### Specialty: 229 labels, three sources, and changes over time
`Prscrbr_Type` comes from Part B claims for most rows (164.8M), from the national provider registry's specialty for 13.5M, and from its taxonomy codes for 3.5M (`Prscrbr_Type_Src`).

- Spelling variants of the same specialty: "Orthopedic Surgery" (25,471 prescribers) and "Orthopaedic Surgery" (4,502); "Family Practice" (137,815) and "Family Medicine" (2,110).
- 138,734 prescribers have more than one specialty across the years. Example: one semaglutide prescriber is listed as "Student in an Organized Health Care Education/Training Program", "Internal Medicine" and "Endocrinology" in different years.
- **Decision (to confirm in the adoption model):** use the specialty in the year of adoption, after harmonising spelling variants.

### Prescribers move
122,598 prescribers appear in more than one state across the years. **Decision:** place each adopter on the map at their location in the year of adoption.

### Coverage rules shape what "adoption" means
Medicare Part D may not pay for drugs used for weight loss. Ozempic and Rybelsus are approved for type 2 diabetes and appear from 2018 and 2020. Wegovy, approved for weight management, appears for only 2 prescribers in 2023, then for 3,956 in 2024, after its March 2024 cardiovascular approval allowed plans to cover it for that use. US adoption in this project therefore means adoption for covered uses, mainly diabetes. People who paid privately are not in the data.

### Catalog quirks
In the CMS catalog (`data.json`) the latest year of each dataset is listed with two API entries (its own and "latest"). data.cms.gov also rate-limits requests (HTTP 429), so downloads retry with backoff.

## Census reference files

### ZIP codes are not areas
CMS gives a 5-digit ZIP code. The Census Bureau's ZIP Code Tabulation Areas (ZCTAs) approximate the areas ZIP codes serve, but not every ZIP has one. 3.2% of prescriber-years have a ZIP code with no ZCTA. The largest are unique ZIP codes assigned to big medical centres:

| ZIP | City | Prescriber-years |
|---|---|---|
| 44195 | Cleveland, OH | 20,146 |
| 37232 | Nashville, TN | 16,162 |
| 21287 | Baltimore, MD | 13,329 |
| 29425 | Charleston, SC | 10,869 |
| 14642 | Rochester, NY | 10,759 |
| 94143 | San Francisco, CA | 10,124 |

Checked whether this biases the adoption map: in 2024, 1.4% of semaglutide prescribers are unmatched (1.3% of 2018's early adopters), against 3.2% of all prescribers. Endocrinologists are more affected (5.0%). **Plan:** geocode the 14,516 distinct unmatched addresses with the Census Geocoder; prescribers outside the US (e.g. Canada, Germany, Japan; 511 rows have no ZIP) are left off the map.

### Formatting quirks
- The 2020 ZCTA gazetteer pads every line and its last column name with trailing spaces (`INTPTLONG` followed by over 100 spaces).
- The ZCTA-to-county relationship file is pipe-separated and starts with an invisible byte-order mark.
- 903 of its 47,863 rows are parts of counties that lie in no ZCTA (blank ZCTA columns).
- 10,186 ZCTAs overlap more than one county. **Decision (to confirm):** assign each ZCTA to the county containing most of its land area.
