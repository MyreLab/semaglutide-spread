-- The UK adopters (the numerator): one row per GP practice that ever prescribed the target
-- drug, with two adoption dates:
--
--   adoption_month          the first month with 1+ item (the UK data's own, finest
--                           measure; used for the maps)
--   matched_adoption_month  the first month in which the past 12 months add up to 11+
--                           items: close to the US rule (11+ claims in a calendar year), for
--                           comparing countries. Null if the practice never reached it.
--
-- Location and NHS area are taken from the month of first adoption.

with monthly as (
    select
        nation,
        month,
        practice_code,
        target_drug_items,
        sum(target_drug_items) over (
            partition by nation, practice_code
            order by month
            range between interval 11 months preceding and current row
        ) as target_drug_items_12m
    from {{ ref('uk_population_at_risk') }}
),

first_months as (
    select
        nation,
        practice_code,
        min(month) filter (where target_drug_items > 0) as adoption_month,
        min(month) filter (where target_drug_items_12m >= 11) as matched_adoption_month
    from monthly
    group by nation, practice_code
    having min(month) filter (where target_drug_items > 0) is not null
)

select
    f.nation,
    f.practice_code,
    f.adoption_month,
    f.matched_adoption_month,
    p.target_drug_items as adoption_month_items,
    p.target_drug_brands as adoption_brands,
    p.practice_name,
    p.postcode,
    p.postcode_status,
    p.latitude,
    p.longitude,
    p.region,
    p.nhs_area_code,
    p.nhs_area_name,
    p.list_size,
    p.local_authority_code,
    p.rural_urban_2021
from first_months as f
inner join {{ ref('uk_population_at_risk') }} as p
    on f.nation = p.nation
    and f.practice_code = p.practice_code
    and f.adoption_month = p.month
