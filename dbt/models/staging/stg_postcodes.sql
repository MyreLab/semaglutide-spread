-- One row per UK practice postcode looked up on postcodes.io.

select
    postcode,
    status as lookup_status,
    cast(latitude as double) as latitude,
    cast(longitude as double) as longitude,
    country,
    region,
    icb_code,
    icb_name,
    local_authority_code,
    local_authority_name,
    lsoa21_code,
    rural_urban_2021,
    terminated as terminated_month
from {{ source('postcodes_io', 'postcodes') }}
