-- Scotland: one row per GP practice x month. A practice code counts as a GP practice if it is
-- in the GP practice register; its details (name, postcode, list size, health board) come
-- from the latest register published at or before that month (the first register, for
-- months before it).

with register as (
    select r.*, b.health_board_name
    from {{ ref('stg_phs__register') }} as r
    left join {{ ref('scottish_health_boards') }} as b using (health_board_code)
),

gp_months as (
    select p.month, p.practice_code, p.items
    from {{ ref('stg_phs__practices') }} as p
    where p.practice_code in (select practice_code from register)
),

with_details as (
    select
        g.month,
        g.practice_code,
        g.items,
        coalesce(r_before.practice_name, r_after.practice_name) as practice_name,
        coalesce(r_before.postcode, r_after.postcode) as postcode,
        coalesce(r_before.list_size, r_after.list_size) as list_size,
        coalesce(r_before.health_board_code, r_after.health_board_code) as health_board_code,
        coalesce(r_before.health_board_name, r_after.health_board_name) as health_board_name
    from gp_months as g
    asof left join register as r_before
        on g.practice_code = r_before.practice_code and g.month >= r_before.quarter
    asof left join register as r_after
        on g.practice_code = r_after.practice_code and g.month <= r_after.quarter
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
    from {{ ref('stg_phs__diabetes_items') }}
    group by month, practice_code
)

select
    w.month,
    w.practice_code,
    w.practice_name,
    w.postcode,
    coalesce(l.lookup_status, 'not_looked_up') as postcode_status,
    l.latitude,
    l.longitude,
    l.region,
    w.health_board_code as nhs_area_code,
    w.health_board_name as nhs_area_name,
    l.local_authority_code,
    l.rural_urban_2021,
    w.list_size,
    w.items as all_items,
    coalesce(d.glucose_lowering_items, 0) as glucose_lowering_items,
    coalesce(d.glucose_lowering_drugs, 0) as glucose_lowering_drugs,
    coalesce(d.target_drug_items, 0) as target_drug_items,
    d.target_drug_brands
from with_details as w
left join diabetes as d using (month, practice_code)
left join {{ ref('stg_postcodes') }} as l using (postcode)
