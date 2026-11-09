-- One row per 2020 ZCTA: its internal point (the map location for prescribers in that ZIP code).

select
    GEOID as zcta,
    cast(INTPTLAT as double) as latitude,
    cast(INTPTLONG as double) as longitude,
    cast(ALAND as bigint) as land_area_m2
from {{ source('census', 'zcta2020_gazetteer') }}
