-- Where semaglutide spread in the UK: for each month, the share of GP practices in the
-- population at risk that have adopted (matched measure: 11+ items in 12 months, as in the
-- US comparison), by area and by how urban or rural the practice is.
-- One row per month x place_type x place.
--
-- place_type 'area': England's nine regions, and Scotland, Wales and Northern Ireland whole.
-- Practices in England with no region (a few retired postcodes) are 'England, region unknown'.
-- place_type 'rurality': England and Wales only, from the ONS 2021 rural-urban
-- classification of the practice's postcode ('Urban' or 'Rural'). Scotland and Northern
-- Ireland use different classifications, so they are left out of this split.

with at_risk as (
    select
        p.month,
        p.nation,
        p.region,
        p.rural_urban_2021,
        a.matched_adoption_month is not null
        and a.matched_adoption_month <= p.month as has_adopted
    from {{ ref('uk_population_at_risk') }} as p
    left join {{ ref('uk_adopters') }} as a using (nation, practice_code)
),

by_place as (
    select
        month,
        'area' as place_type,
        case
            when nation <> 'England' then nation
            else coalesce(region, 'England, region unknown')
        end as place,
        has_adopted
    from at_risk

    union all

    select
        month,
        'rurality' as place_type,
        case
            when rural_urban_2021 like 'Urban%' then 'Urban'
            when rural_urban_2021 ilike '%rural%' then 'Rural'
            else 'Unknown'
        end as place,
        has_adopted
    from at_risk
    where nation in ('England', 'Wales')
)

select
    month,
    place_type,
    place,
    count(*) as at_risk,
    count(*) filter (where has_adopted) as adopted,
    round(100.0 * count(*) filter (where has_adopted) / count(*), 1) as prevalence_pct
from by_place
group by month, place_type, place
order by place_type, place, month
