-- Where semaglutide spread in the US: for each year, the share of the population at risk that
-- has ever adopted, by state and by how urban or rural the prescriber's address is.
-- One row per year x place_type x place.
--
-- place_type 'state': the state of the prescriber's location that year (from its county).
-- place_type 'rurality': the Rural-Urban Commuting Area (RUCA) code of the prescriber's
-- address, as CMS publishes it, grouped by its whole-number part: 1-3 metropolitan, 4-6
-- micropolitan, 7-10 small town and rural; 99 (not coded) and missing are 'Unknown'.

with at_risk as (
    select
        p.year,
        p.county_fips,
        p.ruca_code,
        a.adoption_year is not null and a.adoption_year <= p.year as has_adopted
    from {{ ref('us_population_at_risk') }} as p
    left join {{ ref('us_adopters') }} as a using (npi)
),

by_place as (
    select
        year,
        'state' as place_type,
        coalesce(s.state_name, 'Unknown') as place,
        has_adopted
    from at_risk
    left join {{ ref('us_states') }} as s on left(at_risk.county_fips, 2) = s.state_fips

    union all

    select
        year,
        'rurality' as place_type,
        case
            when try_cast(ruca_code as double) between 1 and 3.99 then 'Metropolitan'
            when try_cast(ruca_code as double) between 4 and 6.99 then 'Micropolitan'
            when try_cast(ruca_code as double) between 7 and 10.99 then 'Small town and rural'
            else 'Unknown'
        end as place,
        has_adopted
    from at_risk
)

select
    year,
    place_type,
    place,
    count(*) as at_risk,
    count(*) filter (where has_adopted) as adopted,
    round(100.0 * count(*) filter (where has_adopted) / count(*), 1) as prevalence_pct
from by_place
group by year, place_type, place
order by place_type, place, year
