"""A tiny, made-up copy of the raw data, in the same folders and columns as the real one.

It lets the whole dbt project (every model and every data test) run in seconds, on a laptop or
in GitHub Actions, without downloading 33 GB. The prescribers below tell a small story with
known answers, so tests can check the results, not only that the build ran.

Three years (2018-2020), ZIP areas in the East Bay, and:

    background   150 family doctors in Oakland (94612) prescribing metformin every year, and
                 50 who prescribe no diabetes drug at all (lisinopril only)
    A  0000000001  endocrinologist, El Cerrito (94530); Ozempic every year       -> adopts 2018
    B  0000000002  internist in Oakland 2018 (metformin); family doctor in El Cerrito from
                   2019 (Ozempic and Rybelsus)                                   -> adopts 2019
    C  0000000003  nurse practitioner at a campus address (ZIP 94720 has no ZIP area, but the
                   address geocodes); metformin 2018-2019, Ozempic 2020           -> adopts 2020
    D  0000000004  dermatologist in a ZIP code with no ZIP area and no geocoded address
                   (94599: placed by its 3-digit area); Ozempic only, 2020        -> adopts 2020
    E  0000000005  cardiologist in Canada; metformin 2019 only (at risk, not on the map)
    F  0000000006  physician assistant at an address the geocoder could not match (94720:
                   placed at the median of matched addresses in that ZIP); Ozempic 2019, then
                   no diabetes drugs in 2020                                     -> adopts 2019
    G  0000000007  an organization whose specialty CMS writes as "None"; metformin 2020
    Z  0000000099  a 2018 prescriber with one row for every drug in the glucose_lowering_drugs
                   seed except semaglutide (so the seed tests find every name in the data)

England, January to June 2019, by prescribing organisation (code) and month:

    A81001  Leeds; metformin every month; 3 Ozempic items a month from February
            -> adopts 2019-02; its past 12 months reach 11 items in May -> matched 2019-05
    B82002  York; metformin every month; one Rybelsus item in April only
            -> adopts 2019-04; never reaches 11 items in 12 months
    Y01008  a practice whose postcode has since been retired; metformin every month; one
            semaglutide item prescribed by generic name in June -> adopts 2019-06
    C83003  prescribes no diabetes drugs (not at risk); its postcode is unknown
    D84004  metformin in January to March, then closes
    -       "unidentified doctors": 2 Ozempic items in March, left out of everything
    Y03127  a community diabetes team (not a GP practice in the NHS register); Ozempic in
            January, left out of the UK tables

Scotland, same months:

    10002   Dundee GP practice (in the register); metformin every month; 2 Ozempic items a
            month from March -> adopts 2019-03; only 8 items by June, so no matched date
    10017   Crieff GP practice; metformin every month; never prescribes semaglutide
    99996   not in the GP practice register (e.g. hospital prescribing); semaglutide in
            January, left out because it is not a GP practice

Northern Ireland, same months:

    1       Belfast GP practice; metformin every month; one semaglutide item prescribed by
            generic name in May -> adopts 2019-05
    2       Newry GP practice; metformin every month
    999     not in a GP practice reference file; left out

Wales, same months:

    W93001  Abercarn GP practice; metformin every month; 2 Ozempic items in March
            -> adopts 2019-03
    W98001  Swansea GP practice; metformin every month
    W00024  a health trust (not a GP practice in the NHS register); semaglutide in April,
            left out
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import duckdb

SEED = Path(__file__).parents[1] / "dbt" / "seeds" / "glucose_lowering_drugs.csv"
YEARS = (2018, 2019, 2020)

PROVIDER_COLUMNS = (
    "Prscrbr_NPI", "Prscrbr_Ent_Cd", "Prscrbr_Crdntls", "Prscrbr_Type", "Prscrbr_Type_Src",
    "Prscrbr_St1", "Prscrbr_St2", "Prscrbr_City", "Prscrbr_State_Abrvtn", "Prscrbr_State_FIPS",
    "Prscrbr_Zip5", "Prscrbr_Cntry", "Prscrbr_RUCA", "Prscrbr_RUCA_Desc",
    "Tot_Clms", "Tot_Drug_Cst", "Tot_Benes",
)  # fmt: skip
DRUG_COLUMNS = (
    "Prscrbr_NPI", "Prscrbr_Type", "Prscrbr_Type_Src", "Brnd_Name", "Gnrc_Name",
    "Tot_Clms", "Tot_30day_Fills", "Tot_Day_Suply", "Tot_Drug_Cst", "Tot_Benes",
)  # fmt: skip

# Addresses: (street 1, street 2, city, state, ZIP, country)
OAKLAND = ("1 Broadway", None, "Oakland", "CA", "94612", "US")
EL_CERRITO = ("10500 San Pablo Ave", None, "El Cerrito", "CA", "94530", "US")
CAMPUS = ("2222 Bancroft Way", None, "Berkeley", "CA", "94720", "US")
CAMPUS_ANNEX = ("Tang Center Annex", None, "Berkeley", "CA", "94720", "US")
NO_ZCTA = ("1 Hidden Valley Rd", None, "Martinez", "CA", "94599", "US")
TORONTO = ("1 Yonge St", None, "Toronto", "ZZ", None, "CA")

# Story prescribers: npi -> {year: (entity code, specialty, address, [(brand, generic, claims)])}
STORY = {
    "0000000001": {
        y: ("I", "Endocrinology", EL_CERRITO, [("Ozempic", "Semaglutide", 20)]) for y in YEARS
    },
    "0000000002": {
        2018: ("I", "Internal Medicine", OAKLAND, [("Metformin", "Metformin Hcl", 30)]),
        2019: (
            "I",
            "Family Practice",
            EL_CERRITO,
            [("Ozempic", "Semaglutide", 15), ("Rybelsus", "Semaglutide", 12)],
        ),
        2020: ("I", "Family Practice", EL_CERRITO, [("Ozempic", "Semaglutide", 40)]),
    },
    "0000000003": {
        2018: ("I", "Nurse Practitioner", CAMPUS, [("Metformin", "Metformin Hcl", 25)]),
        2019: ("I", "Nurse Practitioner", CAMPUS, [("Metformin", "Metformin Hcl", 25)]),
        2020: ("I", "Nurse Practitioner", CAMPUS, [("Ozempic", "Semaglutide", 11)]),
    },
    "0000000004": {
        2020: ("I", "Dermatology", NO_ZCTA, [("Ozempic", "Semaglutide", 13)]),
    },
    "0000000005": {
        2019: ("I", "Cardiology", TORONTO, [("Metformin", "Metformin Hcl", 11)]),
    },
    "0000000006": {
        2019: ("I", "Physician Assistant", CAMPUS_ANNEX, [("Ozempic", "Semaglutide", 18)]),
        2020: ("I", "Physician Assistant", CAMPUS_ANNEX, [("Lisinopril", "Lisinopril", 50)]),
    },
    "0000000007": {
        2020: ("O", "None", OAKLAND, [("Metformin", "Metformin Hcl", 60)]),
    },
}

# ZIP areas: (ZCTA, latitude, longitude, land area, [(county FIPS, county name, land in it)])
ZCTAS = [
    ("94612", "37.8085", "-122.2681", "2000000", [("06001", "Alameda County", "2000000")]),
    (
        "94530",
        "37.9157",
        "-122.2972",
        "11000000",
        [("06013", "Contra Costa County", "10000000"), ("06001", "Alameda County", "1000000")],
    ),
    ("94597", "37.9180", "-122.0700", "9000000", [("06013", "Contra Costa County", "9000000")]),
]

# Geocoder cache: (address, status, latitude, longitude, county)
GEOCODED = [
    (CAMPUS, "Match", "37.8686", "-122.2588", "06001"),
    (CAMPUS_ANNEX, "No_Match", None, None, None),
]


def _story_rows(year: int):
    """Yield (npi, entity code, specialty, address, drugs) for every prescriber in a year."""
    for i in range(150):
        yield f"10{i:08d}", "I", "Family Practice", OAKLAND, [("Metformin", "Metformin Hcl", 30)]
    for i in range(50):
        yield f"20{i:08d}", "I", "Family Practice", OAKLAND, [("Lisinopril", "Lisinopril", 40)]
    for npi, years in STORY.items():
        if year in years:
            yield npi, *years[year]
    if year == 2018:
        with SEED.open() as f:
            names = [
                r["generic_name"] for r in csv.DictReader(f) if r["generic_name"] != "Semaglutide"
            ]
        yield "0000000099", "I", "Endocrinology", OAKLAND, [(n, n, 11) for n in names]


def _write(path: Path, columns: tuple[str, ...], rows: list[tuple]) -> None:
    """Write rows as text-only Parquet, the way the downloaders store raw data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "rows.csv"
        with csv_path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(["" if v is None else v for v in row] for row in rows)
        duckdb.connect().execute(
            f"COPY (SELECT * FROM read_csv('{csv_path}', all_varchar = true, header = true)) "
            f"TO '{path}' (FORMAT parquet)"
        )


ENGLAND_MONTHS = [f"2019-{m:02d}" for m in range(1, 7)]
METFORMIN = ("0601022B0", "Metformin hydrochloride", None, 20)
# code -> (name, postcode, {month: [(chemical code, chemical name, presentation, items)]})
ENGLAND = {
    "A81001": (
        "Leeds Surgery",
        "LS1 1AA",
        {
            m: [METFORMIN]
            + (
                [("0601023AW", "Semaglutide", "Ozempic 1mg/0.74ml inj", 3)]
                if m >= "2019-02"
                else []
            )
            for m in ENGLAND_MONTHS
        },
    ),
    "B82002": (
        "York Practice",
        "YO1 7HH",
        {
            m: [METFORMIN]
            + ([("0601023AW", "Semaglutide", "Rybelsus 3mg tablets", 1)] if m == "2019-04" else [])
            for m in ENGLAND_MONTHS
        },
    ),
    "Y01008": (
        "Bay Medical Group",
        "TQ4 5AA",
        {
            m: [METFORMIN]
            + (
                [("0601023AW", "Semaglutide", "Semaglutide 9mg tablets", 1)]
                if m == "2019-06"
                else []
            )
            for m in ENGLAND_MONTHS
        },
    ),
    "C83003": ("Skin Clinic", "ZZ9 9ZZ", {m: [] for m in ENGLAND_MONTHS}),
    "D84004": ("Closing Practice", "LS1 1AA", {m: [METFORMIN] for m in ENGLAND_MONTHS[:3]}),
    "Y03127": (
        "Diabetes Intermediate Care Team",
        "LS1 1AA",
        {"2019-01": [("0601023AW", "Semaglutide", "Ozempic 1mg/0.74ml inj", 5)]},
    ),
    "-": (
        "UNIDENTIFIED DOCTORS",
        "-",
        {"2019-03": [("0601023AW", "Semaglutide", "Ozempic 1mg/0.74ml inj", 2)]},
    ),
}
POSTCODE_RESULTS = [
    # postcode, status, latitude, longitude, country, region, ICB code, ICB name
    ("LS1 1AA", "current", "53.79", "-1.54", "England", "Yorkshire", "E54000054", "West Yorks"),
    ("YO1 7HH", "current", "53.96", "-1.08", "England", "Yorkshire", "E54000051", "Humber"),
    ("TQ4 5AA", "terminated", "50.43", "-3.56", None, None, None, None),
    ("ZZ9 9ZZ", "not_found", None, None, None, None, None, None),
    ("DD2 5NH", "current", "56.47", "-3.03", "Scotland", None, None, None),
    ("PH7 3SA", "current", "56.37", "-3.84", "Scotland", None, None, None),
    ("BT4 1NS", "current", "54.60", "-5.90", "Northern Ireland", None, None, None),
    ("BT34 1AA", "current", "54.18", "-6.34", "Northern Ireland", None, None, None),
    ("NP11 5GX", "current", "51.65", "-3.14", "Wales", None, None, None),
    ("SA1 1AA", "current", "51.62", "-3.94", "Wales", None, None, None),
]  # fmt: skip

# Scotland: code -> {month: [(chemical code, presentation, items)]}
SCOTLAND = {
    "10002": {
        m: [("0601022B0", None, 30)]
        + ([("0601023AW", "OZEMPIC_INJ 0.25MG/0.19ML 1.5ML PF PEN", 2)] if m >= "2019-03" else [])
        for m in ENGLAND_MONTHS
    },
    "10017": {m: [("0601022B0", None, 25)] for m in ENGLAND_MONTHS},
    "99996": {"2019-01": [("0601023AW", "SEMAGLUTIDE_INJ 0.5MG/0.37ML 1.5ML PFPEN", 4)]},
}
SCOTTISH_REGISTER = [
    ("10002", "MUIRHEAD", "8643", "Liff Road", "", "", "", "DD2 5NH", "17J", "S08000030"),
    ("10017", "THE BLUE PRACTICE", "6685", "King St", "", "", "", "PH7 3SA", "17J", "S08000030"),
]  # fmt: skip


def _write_scotland(raw: Path) -> None:
    for month in ENGLAND_MONTHS:
        practices, diabetes = [], []
        for code, months in SCOTLAND.items():
            if month not in months:
                continue
            rx = months[month]
            practices.append((code, 500 + sum(r[2] for r in rx)))
            diabetes += [
                (code, chem, pres, items, items * 28, items * 5) for chem, pres, items in rx
            ]
        folder = f"year_month={month}"
        _write(
            raw / "phs_practices" / folder / "data.parquet", ("practice_code", "items"), practices
        )
        _write(raw / "phs_diabetes" / folder / "data.parquet",
               ("practice_code", "bnf_chemical_code", "target_presentation", "items",
                "paid_quantity", "gross_ingredient_cost"), diabetes)  # fmt: skip
    _write(
        raw / "phs_register" / "quarter=2019-01" / "data.parquet",
        ("practice_code", "practice_name", "list_size", "address_1", "address_2", "address_3",
         "address_4", "postcode", "practice_type", "health_board"),
        SCOTTISH_REGISTER,
    )  # fmt: skip


def _write_england(raw: Path) -> None:
    for month in ENGLAND_MONTHS:
        practices, diabetes = [], []
        for code, (name, postcode, months) in ENGLAND.items():
            if month not in months:
                continue
            rx = months[month]
            practices.append(
                (code, name, "1 High St", None, None, None, postcode, 100 + sum(r[3] for r in rx))
            )
            diabetes += [
                (code, chem, chem_name, pres, items, items * 28, items * 5, items * 5)
                for chem, chem_name, pres, items in rx
            ]
        folder = f"year_month={month}"
        _write(raw / "epd_practices" / folder / "data.parquet",
               ("practice_code", "practice_name", "address_1", "address_2", "address_3",
                "address_4", "postcode", "items"), practices)  # fmt: skip
        _write(raw / "epd_diabetes" / folder / "data.parquet",
               ("practice_code", "bnf_chemical_code", "bnf_chemical_name", "target_presentation",
                "items", "total_quantity", "nic", "actual_cost"), diabetes)  # fmt: skip
    _write(
        raw / "ods_gp_practices" / "data.parquet",
        ("OrgId", "Name", "Status", "PostCode", "LastChangeDate"),
        [(code, name, "Active", pc, "2024-01-01") for code, (name, pc, _) in ENGLAND.items()
         if code not in ("-", "Y03127")]
        + [(code, name, "Active", pc, "2024-01-01")
           for code, (_, name, pc, _) in WALES.items() if code != "W00024"],
    )  # fmt: skip
    _write(
        raw / "postcodes" / "data.parquet",
        ("postcode", "status", "latitude", "longitude", "country", "region", "icb_code",
         "icb_name", "local_authority_code", "local_authority_name", "lsoa21_code",
         "rural_urban_2021", "terminated", "looked_up_at"),
        [(*r, None, None, None, None, None, None) for r in POSTCODE_RESULTS],
    )  # fmt: skip


# Northern Ireland: code -> {month: [(substance, product, items)]}
NORTHERN_IRELAND = {
    "1": {
        m: [("Metformin", None, 40)]
        + ([("Semaglutide", "Semaglutide 9mg tablets", 1)] if m == "2019-05" else [])
        for m in ENGLAND_MONTHS
    },
    "2": {m: [("Metformin", None, 35)] for m in ENGLAND_MONTHS},
    "999": {"2019-02": [("Semaglutide", "Ozempic 1mg pens", 3)]},
}
NI_REGISTER = [
    ("1", "Dr. HOEY", "HARLAND", "HOLYWOOD ARCHES", "BELFAST", "BT4 1NS", "Belfast", "6783"),
    ("2", "NEWRY CLINIC", "1 HILL ST", "", "NEWRY", "BT34 1AA", "Southern", "5120"),
]  # fmt: skip


def _write_northern_ireland(raw: Path) -> None:
    for month in ENGLAND_MONTHS:
        practices, diabetes = [], []
        for code, months in NORTHERN_IRELAND.items():
            if month not in months:
                continue
            rx = months[month]
            practices.append((code, str(300 + sum(r[2] for r in rx))))
            diabetes += [(code, sub, prod, items, items * 28, items * 5, items * 5)
                         for sub, prod, items in rx]  # fmt: skip
        folder = f"year_month={month}"
        _write(
            raw / "ni_practices" / folder / "data.parquet", ("practice_code", "items"), practices
        )
        _write(
            raw / "ni_diabetes" / folder / "data.parquet",
            ("practice_code", "substance", "target_presentation", "items", "quantity",
             "gross_cost", "actual_cost"),
            diabetes,
        )  # fmt: skip
    _write(
        raw / "ni_register" / "year_month=2019-01" / "data.parquet",
        ("practice_code", "practice_name", "address_1", "address_2", "address_3", "postcode",
         "lcg", "registered_patients"),
        NI_REGISTER,
    )  # fmt: skip


# Wales: code -> (health board, name, postcode, {month: [(chemical code, product, items)]})
WALES = {
    "W93001": ("7A6", "ST. LUKE'S SURGERY", "NP11 5GX", {
        m: [("0601022B0", None, 30)]
        + ([("0601023AW", "Ozempic_Inj 1mg/0.74ml", 2)] if m == "2019-03" else [])
        for m in ENGLAND_MONTHS
    }),
    "W98001": ("7A3", "RIVERSIDE SURGERY", "SA1 1AA", {
        m: [("0601022B0", None, 25)] for m in ENGLAND_MONTHS
    }),
    "W00024": ("7A6", "GWENT HEALTHCARE TRUST", "NP11 5GX", {
        "2019-04": [("0601023AW", "Semaglutide_Inj 1mg/0.74ml", 6)]
    }),
}  # fmt: skip


def _write_wales(raw: Path) -> None:
    for month in ENGLAND_MONTHS:
        practices, diabetes, register = [], [], []
        for code, (board, name, postcode, months) in WALES.items():
            if month not in months:
                continue
            rx = months[month]
            practices.append((code, board, str(200 + sum(r[2] for r in rx))))
            diabetes += [(code, board, chem, prod, items, items * 28, items * 5, items * 5)
                         for chem, prod, items in rx]  # fmt: skip
            register.append((code, name, "", "", "", postcode))
        folder = f"year_month={month}"
        _write(raw / "wales_practices" / folder / "data.parquet",
               ("practice_code", "health_board", "items"), practices)  # fmt: skip
        _write(
            raw / "wales_diabetes" / folder / "data.parquet",
            ("practice_code", "health_board", "bnf_chemical_code", "target_presentation",
             "items", "quantity", "nic", "actual_cost"),
            diabetes,
        )  # fmt: skip
        _write(
            raw / "wales_register" / folder / "data.parquet",
            ("practice_code", "practice_name", "address_2", "address_3", "address_4", "postcode"),
            register,
        )


def write_sample_raw_data(data_dir: Path) -> Path:
    """Write the sample raw data under data_dir/raw/ and return data_dir."""
    raw = data_dir / "raw"
    for year in YEARS:
        providers, drugs = [], []
        for npi, entity, specialty, (st1, st2, city, state, zip5, country), rx in _story_rows(year):
            claims = sum(c for _, _, c in rx)
            providers.append(
                (npi, entity, "MD", specialty, "S", st1, st2, city, state, "06", zip5, country,
                 "1", "Metropolitan area core", claims, claims * 10, None)
            )  # fmt: skip
            drugs += [
                (npi, specialty, "S", brand, generic, c, c, c * 30, c * 10, None)
                for brand, generic, c in rx
            ]
        _write(
            raw / "partd_by_provider" / f"year={year}" / "data.parquet", PROVIDER_COLUMNS, providers
        )
        _write(
            raw / "partd_by_provider_and_drug" / f"year={year}" / "data.parquet",
            DRUG_COLUMNS,
            drugs,
        )

    _write(
        raw / "census_zcta2020_gazetteer" / "data.parquet",
        ("GEOID", "ALAND", "INTPTLAT", "INTPTLONG"),
        [(z, land, lat, lon) for z, lat, lon, land, _ in ZCTAS],
    )
    _write(
        raw / "census_zcta2020_counties" / "data.parquet",
        ("GEOID_ZCTA5_20", "GEOID_COUNTY_20", "NAMELSAD_COUNTY_20", "AREALAND_PART"),
        [(z, fips, name, part) for z, *_, parts in ZCTAS for fips, name, part in parts],
    )
    _write(
        raw / "census_geocoder" / "data.parquet",
        ("st1", "st2", "city", "state", "zip5", "status", "matched_line", "match_type",
         "matched_address", "latitude", "longitude", "county_fips"),
        [
            (*address[:5], status, line, None, None, lat, lon, county)
            for address, status, lat, lon, county in GEOCODED
            for line in ["st1" if status == "Match" else None]
        ],
    )  # fmt: skip
    _write_england(raw)
    _write_scotland(raw)
    _write_northern_ireland(raw)
    _write_wales(raw)
    return data_dir
