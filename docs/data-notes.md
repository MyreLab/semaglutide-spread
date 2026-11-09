# Data notes

Problems found in the source data, what they look like, and how this project handles them. Each entry has a real example and the effect on the results. Figures are for Medicare Part D 2018-2024 unless stated.

## Medicare Part D (CMS)

### Rows are aggregates, and small counts are removed
Each row of "by Provider and Drug" is one prescriber x one drug (brand) x one year. Rows with fewer than 11 claims are not published.

- **Effect on "adoption":** suppression applies to each prescriber x brand row separately. A prescriber with 6 Ozempic and 6 Rybelsus claims (12 semaglutide claims) does not appear at all. Adoption is therefore defined as *the first year with 11+ claims for at least one semaglutide brand*.
- **Wording:** the 181.8 million rows represent 9.38 billion claims (sum of `Tot_Clms`). The project never handles individual claims.

### Beneficiary counts are suppressed separately
`Tot_Benes` (distinct patients) is blank in 103.5 million of the 181.8 million rows (57%): CMS also hides patient counts below 11, even when the row has 11 or more claims. This project counts prescribers and claims, not patients.

### Not every "generic name" is a drug
Searching the generic names for diabetes drugs also finds devices and supplies, and drugs that only share letters:

- Devices: insulin syringes ("Syringe-Needle,insulin,0.5 Ml"), pump cartridges ("Insulin Pump Cart,auto,bt,g6/7", sold as Omnipod), reusable pens, insulin patches (V-Go)
- Metolazone, a diuretic, which contains "tolaz" like the old sulfonylurea tolazamide
- Colesevelam and bromocriptine: each has a brand approved for type 2 diabetes, but both are mostly prescribed for other conditions (cholesterol; Parkinson's and prolactin disorders), and Part D's generic name doesn't tell the uses apart

**Decision:** the list of glucose-lowering drugs (`dbt/seeds/glucose_lowering_drugs.csv`) includes exactly the drugs in WHO ATC group A10, "Drugs used in diabetes": 70 of the 93 matching generic names. The other 23 are listed as excluded, each with a reason. A test fails if CMS publishes a new diabetes-like generic name that isn't classified, and another if a listed name doesn't exist in Part D.

SGLT2 inhibitors (empagliflozin, dapagliflozin) are also prescribed for heart failure and kidney disease, so cardiologists and nephrologists who prescribe them are in the population at risk. That is intended: they could prescribe semaglutide too, and since 2024 it has a cardiovascular indication.

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

### The "2020" ZIP-area file lists the 2010 ZIP areas
ZIP Code Tabulation Areas (ZCTAs) are redrawn after each census. The Census Bureau's file named "2020 Gazetteer" has 33,144 ZCTAs: the **2010** set. The 2020 set (33,791 ZCTAs, matching the 2020 ZCTA-to-county file) first appears in the 2021 Gazetteer:

| Gazetteer release | ZCTAs | Same land areas as the 2020 county file |
|---|---|---|
| 2020 | 33,144 | 511 |
| 2021 | 33,791 | 30,131 |
| 2022 | 33,791 | 24,293 |
| 2024 | 33,791 | 17,253 |

This project first used the 2020 file; a test comparing the two Census files caught the mismatch (851 ZCTAs in the county file were missing from the gazetteer). It now uses the 2021 release, the closest to the 2020 county file, and the downloader checks the row count is exactly 33,791. Every figure below uses the 2020 ZCTAs.

### ZIP codes are not areas
CMS gives a 5-digit ZIP code. ZCTAs approximate the areas ZIP codes serve, but not every ZIP has one. 1.84% of prescriber-years have a ZIP code with no ZCTA. The largest are unique ZIP codes assigned to big medical centres:

| ZIP | City | Prescriber-years |
|---|---|---|
| 44195 | Cleveland, OH | 20,146 |
| 94143 | San Francisco, CA | 10,139 |
| 27157 | Winston-Salem, NC | 9,309 |
| 01655 | Worcester, MA | 8,698 |
| 68198 | Omaha, NE | 7,874 |
| 03756 | Lebanon, NH | 7,490 |

Checked whether this biases the adoption map: in 2024, 0.73% of semaglutide prescribers have a ZIP code with no ZCTA (1.03% of 2018's early adopters), against 1.82% of all prescribers. Endocrinologists are more affected (3.14%).

### Geocoding the rest
The 12,391 distinct addresses behind those prescriber-years (in the 50 states, DC and the territories) are sent to the Census Bureau's batch geocoder. Military mail (APO/FPO/DPO) and foreign addresses are not: they have no US street location.

| Result | Addresses |
|---|---|
| Matched on street line 1 | 6,205 |
| Matched on street line 2, after line 1 failed | 709 |
| Two equally good candidates ("Tie"), so no location | 454 |
| No match | 5,023 |

Line 2 helps because many records put a department or building in line 1 and the street in line 2 (e.g. "Kent State University Health Services" / "Eastway Drive"). Addresses that fail are mostly not street addresses: "Department Of Psychiatry Child Psychiatry", "988480 Nebraska Medical Ctr", PO boxes.

Placement of all 9.12 million prescriber-years after geocoding:

| Method | Share |
|---|---|
| ZCTA centre point | 98.15% |
| Geocoded street address | 1.19% |
| Not placed (geocoding failed) | 0.63% |
| Not attempted (military mail, foreign, no ZIP) | 0.03% |

Among semaglutide prescribers, 0.27% are not placed by these two methods in 2024 and 0.38% in 2018. The failures concentrate in a few medical-centre ZIP codes (03756 Lebanon NH, 27157 Winston-Salem, 68198 Omaha, 06030 Farmington CT, 87131 Albuquerque).

### Fallbacks, and the final placement
The prescriber location model (`int_prescriber_locations`) places each prescriber-year with the first method that works:

| Method | Prescriber-years | Share |
|---|---|---|
| 1. ZCTA internal point | 8,949,272 | 98.15% |
| 2. Geocoded street address | 108,544 | 1.19% |
| 3. Median of geocoded addresses with the same ZIP code | 52,149 | 0.57% |
| 4. Median location of prescribers sharing the first three ZIP digits | 5,142 | 0.06% |
| Not placed | 2,909 | 0.03% |

Method 3 works because a medical centre's unique ZIP code usually has some addresses the geocoder could match: for 03756 (Dartmouth Health, Lebanon NH), the failed addresses get the median point of the ones that matched. Method 4 uses the first three digits, which identify a postal sorting area, and takes the median location of prescribers there who were placed by method 1.

Not placed: military mail (1,487), addresses outside the US (1,188), no ZIP code (10), and 224 whose ZIP code has no ZCTA within its sorting area. Most of these 224 are military ZIP codes (09xxx and 962xx, used for overseas bases) that CMS lists with a state, e.g. "09244, NY". In 2024, 18 of 174,885 semaglutide prescribers (0.01%) are not placed. A test fails the build if more than 1% of any year is not placed.

Each ZCTA is assigned to the 2020 county holding most of its land; geocoded addresses carry their own county. In 631 placed prescriber-years (0.007%), the assigned county is in a different state from CMS's state code, including the inconsistent rows below.

Of the geocoded matches, 75% are "Non_Exact" (the geocoder adjusted the address, for example dropping a suite number or correcting the ZIP code). For a national map, a street-level match of either kind is more precise than a ZIP-area centre point.

### State code and state name disagree
In 3,155 prescriber-years (0.03%), CMS's state FIPS code and state abbreviation point to different states. Examples: "Houston, TN" with Texas's code and a Houston, Texas ZIP code (77079); "Fort Campbell, KY" with Tennessee's code (Fort Campbell straddles the border). Location is taken from the ZIP code or street address, not from either state field, so this does not move dots on the map; state-level summaries will use the state of the assigned location.

### Formatting quirks
- The ZCTA gazetteer pads every line and its last column name with trailing spaces (`INTPTLONG` followed by over 100 spaces). The 2025 release switched from tabs to pipes and added a column.
- The ZCTA-to-county relationship file is pipe-separated and starts with an invisible byte-order mark.
- 903 of its 47,863 rows are parts of counties that lie in no ZCTA (blank ZCTA columns).
- 10,186 ZCTAs overlap more than one county. **Decision (to confirm):** assign each ZCTA to the county containing most of its land area.
