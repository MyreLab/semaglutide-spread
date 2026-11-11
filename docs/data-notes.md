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

### Specialty: 264 labels, three sources, and changes over time
`Prscrbr_Type` comes from Part B claims for most rows (164.8M), from the national provider registry's specialty for 13.5M, and from its taxonomy codes for 3.5M (`Prscrbr_Type_Src`). The "by Provider" file has 264 distinct labels.

- Spelling variants of the same specialty: "Orthopedic Surgery" (25,471 prescribers) and "Orthopaedic Surgery" (4,502); "Family Practice" (137,815) and "Family Medicine" (2,110); "Physical Medicine and Rehabilitation" and "Physical Medicine & Rehabilitation".
- 25 prescribers have the literal word "None" as their specialty. Loaded naively, it turns into a missing value in some tools and stays as text in others; staging converts it to a missing value explicitly.
- Labels that are not clinical specialties at all: "Driver", "Interpreter", "Lodging", "Home Delivered Meals", "Veterinarian". They come from the national provider registry and cover a handful of prescribers each.
- Prescribers change label over time: one semaglutide prescriber is listed as "Student in an Organized Health Care Education/Training Program", "Internal Medicine" and "Endocrinology" in different years. After harmonising, 170,542 prescribers still appear in more than one specialty group.

**Decision:** `dbt/seeds/specialty_groups.csv` maps every label to a harmonised specialty (263 labels to 242 names) and one of 13 groups: primary care physician, advanced practice nurse, physician assistant, endocrinology, cardiology, nephrology, obstetrics and gynecology, hospital and emergency medicine, physician in training, other physician specialty, pharmacist, other prescriber, and organization or unknown. A test fails if CMS publishes a label that isn't in the seed. The adoption model will use the specialty in the year of adoption.

Semaglutide prescribers by group, 2018 and 2024:

| Group | All prescribers 2024 | Semaglutide 2024 | Semaglutide 2018 |
|---|---|---|---|
| Primary care physician | 270,518 | 101,196 | 480 |
| Advanced practice nurse | 283,915 | 43,635 | 165 |
| Physician assistant | 137,246 | 15,133 | 64 |
| Endocrinology | 6,689 | 5,750 | 330 |
| Cardiology | 28,045 | 2,634 | 11 |
| Other groups | 690,470 | 6,537 | 15 |

### Prescribers move
122,598 prescribers appear in more than one state across the years. **Decision:** place each adopter on the map at their location in the year of adoption.

### Coverage rules shape what "adoption" means
Medicare Part D may not pay for drugs used for weight loss. Ozempic and Rybelsus are approved for type 2 diabetes and appear from 2018 and 2020. Wegovy, approved for weight management, appears for only 2 prescribers in 2023, then for 3,956 in 2024, after its March 2024 cardiovascular approval allowed plans to cover it for that use. US adoption in this project therefore means adoption for covered uses, mainly diabetes. People who paid privately are not in the data.

### What "first year" can and cannot mean
- **It is the first year a prescriber becomes visible, not their first prescription.** A prescriber who wrote 5 semaglutide prescriptions in 2019 and 40 in 2020 adopts in 2020 here. Early, light use is hidden by the 11-claim rule, so adoption years lean later than the real first prescription, most for prescribers who start slowly.
- **Adoption can mean one or two patients.** A claim is one prescription fill, refills included, so one patient filling a monthly prescription for a year makes 12 claims: enough to pass the 11-claim rule. In their adoption year, the median adopter had 16 to 20 semaglutide claims (depending on the year), and 89% had a patient count too small for CMS to publish (fewer than 11 for every brand). The data cannot say how many patients each adopter had, or whether they were new to the drug: it has no patient-level records. Each light on the map is one prescriber, however many patients or refills they had.
- **2018 is the true start.** Ozempic was approved in December 2017 and launched in early 2018, so the data begin at the drug's launch: nobody adopted before the first year of data. (For the UK this has to be checked separately.)
- **Adoption is counted once.** A prescriber who prescribes semaglutide in 2019, stops, and starts again in 2023 adopted in 2019.
- **The population at risk changes every year.** Prescribers retire, start practice, or stop prescribing diabetes drugs. Each year's denominator is that year's prescribers of any glucose-lowering drug; adopters who stop prescribing diabetes drugs drop out of it. `cumulative_adopters` in `rpt_adoption_curve` counts everyone who has ever adopted, wherever they are now.

The result, 2018-2024 (`rpt_adoption_curve`):

| Year | Population at risk | New adopters | Adopted this year, of those not yet adopted | Ever adopted, of this year's population at risk |
|---|---|---|---|---|
| 2018 | 248,364 | 1,065 | 0.4% | 0.4% |
| 2019 | 252,322 | 11,798 | 4.7% | 5.1% |
| 2020 | 256,006 | 16,287 | 6.7% | 11.3% |
| 2021 | 263,017 | 22,872 | 9.7% | 19.4% |
| 2022 | 278,371 | 35,016 | 15.3% | 30.4% |
| 2023 | 298,483 | 63,477 | 29.2% | 48.4% |
| 2024 | 316,559 | 51,114 | 28.5% | 59.6% |

201,629 prescribers adopted between 2018 and 2024. Out of every Part D prescriber, whatever they prescribe (the sensitivity analysis), 14.0% had adopted by 2024.

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

## English Prescribing Dataset (NHS Business Services Authority)

### Asking the portal instead of downloading the files
Each month of the English Prescribing Dataset is ~17 million rows (~7 GB of CSV). The NHSBSA portal can run SQL on its side, so the downloader asks only for diabetes drugs (BNF 6.1.1 insulins and 6.1.2 antidiabetic drugs), already added up by practice and drug, plus each practice's address and total items. January 2018 to August 2026 is 104 months, 17.7 million diabetes rows and 502 million diabetes items, out of 10.2 billion items of all drugs.

### Results come in pages, and must add up
The portal returns at most 32,000 rows per request, so each month comes in about six pages, in a fixed order. A separate one-row query gives the month's total items, and the pages must match it exactly, or the download stops.

### Two datasets, and the columns changed meaning
NHSBSA retired the original dataset (January 2014 to June 2025) and replaced it with "EPD with SNOMED code" (November 2020 onwards). The downloader takes each month from the newer dataset when it has it: 34 months come from the retired dataset and 70 from the new one.

From March 2025, the column `BNF_CHEMICAL_SUBSTANCE` holds the drug's *name*; before, it held the drug's *code* (the name was in `CHEMICAL_SUBSTANCE_BNF_DESCR`). Reading the wrong one would silently produce zero diabetes rows. The downloader checks each month's columns and picks the matching layout, and stops on any layout it doesn't know.

In some months the costs are stored as text rather than numbers, so every value is converted before it is added up.

### NHS areas were renamed and redrawn
Older months give each practice's STP (sustainability and transformation partnership); newer ones its ICB (integrated care board), which replaced STPs in July 2022. Areas are therefore not taken from the prescribing data: every practice gets its area from its postcode, the same way for every month.

### "Unidentified doctors"
Prescriptions that couldn't be traced to a practice are under the code "-" or codes ending in "998", named "UNIDENTIFIED DOCTORS": 4.8 million items (0.05%). They are left out.

### Nothing is suppressed
Unlike Part D, England publishes every item, even a single prescription, so adoption can be dated to the month. The first semaglutide items are in December 2018 (2 items).

### All brands share one code; many prescriptions don't name a brand
Ozempic, Rybelsus and Wegovy are all BNF chemical substance 0601023AW. Many prescriptions are written by generic name ("Semaglutide 9mg tablets"), so the brand isn't known: about 30% of England's semaglutide items. The product name is kept for semaglutide rows so brands can be split where they are known.

### Two adoption dates
Because single items are published, England (and Scotland and Northern Ireland) get two adoption dates: the first month with any item, used for maps, and the first month whose past 12 months add up to 11 or more items, close to the US rule, used to compare countries. The first is always earlier: by December 2019, 57% of English GP practices had prescribed semaglutide at least once, but only 25% had reached 11 items in 12 months.

## UK postcodes (postcodes.io)

### Most practice postcodes are current; a few are retired or unknown
Practices in all three nations are placed by their postcode, looked up on postcodes.io (built on the ONS Postcode Directory). Of 10,364 distinct practice postcodes, 10,157 are current, 194 have been retired (terminated) and 13 are not known at all.

- **Retired postcodes** belong mostly to practices that closed or moved. postcodes.io keeps their location, so they are still placed on the map, but without areas (region, NHS area, rural-urban class).
- **Unknown postcodes** (13, all in England) look like typos in the source data (e.g. a valid-looking postcode that never existed). Those practices have no location; they still count in every total, but not on maps.
- Values that aren't postcodes at all, like "-" for unidentified prescribing, are dropped before the lookup.

## Prescriptions in the Community (Public Health Scotland)

### Months are when the pharmacy was paid
Scotland's monthly files are by *paid* month: when the dispensing was paid for, usually the month after the medicine was dispensed. England's and Northern Ireland's are by dispensing month. Comparisons between nations can be off by about a month.

### Not every prescribing code is a GP practice
The "prescriber location" files include codes for hospitals and "unallocated" prescribing (99996, 99997, 99998, 99999, among others): 297 of 1,320 codes, and 6.7% of all items. Only codes in Public Health Scotland's GP practice register count as GP practices here.

### The practice register changed its column names
The quarterly register has used `PracticeName` and `GPPracticeName`, `Listsize` and `PracticeListSize`, over the years. The downloader maps each to one name and stops if a needed column disappears. Three practices have a list size of 0 in some quarters (closing or newly opened practices).

### Health boards were re-coded in 2018-2019
Fife and Tayside (February 2018), and Greater Glasgow and Clyde and Lanarkshire (April 2019) got new codes when their boundaries were adjusted. Both old and new codes are in the health board lookup, with their dates, so practices keep a board name throughout.

### Semaglutide arrives in August 2019, all at once
The Scottish Medicines Consortium accepted semaglutide (Ozempic) for restricted use on 14 January 2019, but no Scottish GP practice prescribed it until the August 2019 paid month, when 268 practices (28%) started in the same month. It isn't hidden under another code before then: total GLP-1 prescribing doesn't jump in August. The cause (local formulary decisions are the likely candidate) isn't established here.

### Rybelsus is Scotland's most prescribed form
Of all semaglutide items from 2019 to mid-2026, 385,000 were Rybelsus tablets, 310,000 Ozempic injections, 10,000 Wegovy, and 28,000 prescribed by generic name. In England, Ozempic leads.

## NHS Organisation Data Service (England's GP practices)

### England's data list every prescribing organisation, not only GP practices
The English Prescribing Dataset has a row for every "prescribing cost centre": GP practices, but also walk-in centres, out-of-hours services, prisons, community diabetes teams and groups of nurse prescribers. Their codes can't be told apart reliably: new GP practices get codes starting with "Y", like many of the other services.

**Decision:** keep only codes that the NHS Organisation Data Service lists as GP practices (role RO76), open or closed: 8,256 codes, 6,655 of them still open. What's left out is 0.13% of England's diabetes items, mostly from community diabetes teams and non-medical prescribers.

### The API rejects "Offset=0"
The ODS API counts results from 0 but returns an error for `Offset=0`; the first page is requested without an offset. A small thing, but the kind that makes a pipeline fail on its first run.

## GP Prescribing Data (Northern Ireland)

### Files are Latin-1, not UTF-8
The monthly CSVs use the Latin-1 text encoding: the "£" in `Gross Cost (£)` is a single byte that isn't valid UTF-8. They're read with the encoding stated explicitly, so nothing is replaced or lost.

### BNF codes aren't the standard format
Northern Ireland's `BNF Code` column holds codes like `20030100526`, not the 15-character BNF codes used in England and Scotland. The files also have separate BNF chapter, section and paragraph columns, which are used to select insulins (6.1.1) and antidiabetic drugs (6.1.2), and a substance name (`VTM_NM`, from the NHS dictionary of medicines), which identifies semaglutide.

### "-" means "prescribed by generic name"
The branded product column (`AMP_NM`) is "-" when a prescription was written by generic name; the generic product name (`VMP_NM`) is used instead, and the brand is recorded as unknown.

### No query service for recent months
Many months can be queried on the portal, but about 40 since 2021 (most of them since 2023) can only be downloaded as files (~60 MB each). Every month is downloaded, filtered locally and deleted, so all months are handled the same way.

### Semaglutide arrives in July 2019, all at once
As in Scotland, there is no semaglutide in Northern Ireland until July 2019, when 82 GP practices start in the same month. England's first items were in December 2018.

### Practice reference files
Quarterly files give each practice's address, postcode, local commissioning group (LCG) and registered patients. The LCG is written as the text "NULL" for some practices; it's treated as missing.
