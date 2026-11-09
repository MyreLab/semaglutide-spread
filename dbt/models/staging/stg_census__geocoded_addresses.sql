-- One row per prescriber address sent to the Census geocoder, matched or not.
-- The five address columns are exactly as in Part D, so they join back to
-- stg_partd__prescribers on (street_1, street_2, city, state, zip5).

select
    st1 as street_1,
    st2 as street_2,
    city,
    state,
    zip5,
    status as geocode_status,
    matched_line,
    match_type,
    matched_address,
    cast(latitude as double) as latitude,
    cast(longitude as double) as longitude,
    county_fips
from {{ source('census', 'geocoder') }}
