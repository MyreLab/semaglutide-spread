-- A placed prescriber-year has a latitude, longitude and 5-digit county; an unplaced one has
-- none of them and says why.

select year, npi, location_method
from {{ ref('int_prescriber_locations') }}
where
    (
        location_method <> 'unplaced'
        and (
            latitude is null or longitude is null or length(county_fips) is distinct from 5
            or unplaced_reason is not null
        )
    )
    or (
        location_method = 'unplaced'
        and (latitude is not null or county_fips is not null or unplaced_reason is null)
    )
