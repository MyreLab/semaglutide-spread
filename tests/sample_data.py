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
    return data_dir
