-- One row per part of a 2020 ZCTA lying in a 2020 county, with that part's land area.
-- Rows for county land that lies in no ZCTA (blank ZCTA columns) are left out.

select
    GEOID_ZCTA5_20 as zcta,
    GEOID_COUNTY_20 as county_fips,
    NAMELSAD_COUNTY_20 as county_name,
    cast(AREALAND_PART as bigint) as land_area_m2
from {{ source('census', 'zcta2020_counties') }}
where GEOID_ZCTA5_20 is not null
