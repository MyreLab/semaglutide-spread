-- One row per prescriber x year: where to put the prescriber on the map, and in which county.
--
-- Each prescriber-year is placed by the first method that works:
--   1. zcta_centroid        the ZIP code is a 2020 ZCTA: its internal point and main county
--   2. geocoded_address     the street address was matched by the Census geocoder
--   3. zip_geocoded_median  another address with the same ZIP code was geocoded: the median
--                           point (and most common county) of those addresses
--   4. zip3_median          the median location of prescribers placed by method 1 whose ZIP
--                           code shares the first three digits (same postal sorting area)
--   5. unplaced             outside the US, military mail, no ZIP code, or no nearby ZCTA

with prescribers as (
    select year, npi, street_1, street_2, city, state, zip5, country
    from {{ ref('stg_partd__prescribers') }}
),

zctas as (
    select zcta, latitude, longitude, county_fips
    from {{ ref('int_zcta_primary_county') }}
),

geocoded as (
    select street_1, street_2, city, state, zip5, latitude, longitude, county_fips
    from {{ ref('stg_census__geocoded_addresses') }}
    where geocode_status = 'Match'
),

zip_geocoded_median as (
    select
        zip5,
        median(latitude) as latitude,
        median(longitude) as longitude,
        mode(county_fips) as county_fips
    from geocoded
    group by zip5
),

zip3_median as (
    select
        left(p.zip5, 3) as zip3,
        median(z.latitude) as latitude,
        median(z.longitude) as longitude,
        mode(z.county_fips) as county_fips
    from prescribers as p
    inner join zctas as z on z.zcta = p.zip5
    group by left(p.zip5, 3)
),

joined as (
    select
        p.year,
        p.npi,
        p.zip5,
        p.state as cms_state,
        case
            when coalesce(p.country, '') <> 'US' then 'outside_us'
            when p.state in ('AA', 'AE', 'AP') then 'military_mail'
            when p.zip5 is null then 'no_zip_code'
        end as excluded_reason,
        z.zcta is not null as has_zcta,
        g.latitude as g_lat, g.longitude as g_lon, g.county_fips as g_county,
        z.latitude as z_lat, z.longitude as z_lon, z.county_fips as z_county,
        m5.latitude as m5_lat, m5.longitude as m5_lon, m5.county_fips as m5_county,
        m3.latitude as m3_lat, m3.longitude as m3_lon, m3.county_fips as m3_county
    from prescribers as p
    left join zctas as z on z.zcta = p.zip5
    left join geocoded as g
        on z.zcta is null
        and g.street_1 is not distinct from p.street_1
        and g.street_2 is not distinct from p.street_2
        and g.city is not distinct from p.city
        and g.state is not distinct from p.state
        and g.zip5 is not distinct from p.zip5
    left join zip_geocoded_median as m5 on m5.zip5 = p.zip5
    left join zip3_median as m3 on m3.zip3 = left(p.zip5, 3)
),

methods as (
    select
        *,
        case
            when excluded_reason is not null then 'unplaced'
            when has_zcta then 'zcta_centroid'
            when g_lat is not null then 'geocoded_address'
            when m5_lat is not null then 'zip_geocoded_median'
            when m3_lat is not null then 'zip3_median'
            else 'unplaced'
        end as location_method
    from joined
)

select
    year,
    npi,
    zip5,
    cms_state,
    location_method,
    case
        when location_method = 'unplaced' then coalesce(excluded_reason, 'no_nearby_zcta')
    end as unplaced_reason,
    case location_method
        when 'zcta_centroid' then z_lat
        when 'geocoded_address' then g_lat
        when 'zip_geocoded_median' then m5_lat
        when 'zip3_median' then m3_lat
    end as latitude,
    case location_method
        when 'zcta_centroid' then z_lon
        when 'geocoded_address' then g_lon
        when 'zip_geocoded_median' then m5_lon
        when 'zip3_median' then m3_lon
    end as longitude,
    case location_method
        when 'zcta_centroid' then z_county
        when 'geocoded_address' then g_county
        when 'zip_geocoded_median' then m5_county
        when 'zip3_median' then m3_county
    end as county_fips
from methods
