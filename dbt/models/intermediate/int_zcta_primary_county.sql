-- One row per 2020 ZCTA: its internal point, and the 2020 county holding most of its land.
-- 10,186 ZCTAs cross county lines; the largest land share is the most likely location.

with ranked as (
    select
        zcta,
        county_fips,
        county_name,
        land_area_m2,
        row_number() over (
            partition by zcta order by land_area_m2 desc, county_fips
        ) as rank_by_land
    from {{ ref('stg_census__zcta_counties') }}
)

select
    c.zcta,
    c.latitude,
    c.longitude,
    r.county_fips,
    r.county_name
from {{ ref('stg_census__zcta_centroids') }} as c
inner join ranked as r on r.zcta = c.zcta and r.rank_by_land = 1
