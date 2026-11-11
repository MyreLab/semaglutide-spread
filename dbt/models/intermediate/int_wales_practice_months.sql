-- Wales: one row per GP practice x month. As for England, a code counts as a GP practice if
-- the NHS Organisation Data Service lists it as one. Name and postcode come from that month's
-- address file; the health board from the prescribing file.

with gp_months as (
    select p.month, p.practice_code, p.health_board_code, p.items
    from {{ ref('stg_wales__practices') }} as p
    where p.practice_code in (select practice_code from {{ ref('stg_ods__gp_practices') }})
),

diabetes as (
    select
        month,
        practice_code,
        sum(items) as glucose_lowering_items,
        count(distinct bnf_chemical_code) as glucose_lowering_drugs,
        coalesce(
            sum(items) filter (where bnf_chemical_code = '{{ var("target_bnf_chemical_code") }}'),
            0
        ) as target_drug_items,
        string_agg(distinct target_brand, ', ' order by target_brand) as target_drug_brands
    from {{ ref('stg_wales__diabetes_items') }}
    group by month, practice_code
)

select
    g.month,
    g.practice_code,
    r.practice_name,
    r.postcode,
    coalesce(l.lookup_status, 'not_looked_up') as postcode_status,
    l.latitude,
    l.longitude,
    l.region,
    g.health_board_code as nhs_area_code,
    b.health_board_name as nhs_area_name,
    l.local_authority_code,
    l.rural_urban_2021,
    cast(null as integer) as list_size,  -- not in the Welsh extract
    g.items as all_items,
    coalesce(d.glucose_lowering_items, 0) as glucose_lowering_items,
    coalesce(d.glucose_lowering_drugs, 0) as glucose_lowering_drugs,
    coalesce(d.target_drug_items, 0) as target_drug_items,
    d.target_drug_brands
from gp_months as g
left join {{ ref('stg_wales__register') }} as r using (month, practice_code)
left join {{ ref('welsh_health_boards') }} as b using (health_board_code)
left join diabetes as d using (month, practice_code)
left join {{ ref('stg_postcodes') }} as l on r.postcode = l.postcode
