-- Northern Ireland: one row per GP practice x month. A practice code counts if it is in a GP
-- practice reference file; its details come from the latest file released at or before that
-- month (the first file, for months before it).

with register as (
    select * from {{ ref('stg_ni__register') }}
),

gp_months as (
    select p.month, p.practice_code, p.items
    from {{ ref('stg_ni__practices') }} as p
    where p.practice_code in (select practice_code from register)
),

with_details as (
    select
        g.month,
        g.practice_code,
        g.items,
        coalesce(r_before.practice_name, r_after.practice_name) as practice_name,
        coalesce(r_before.postcode, r_after.postcode) as postcode,
        coalesce(r_before.registered_patients, r_after.registered_patients) as list_size,
        coalesce(r_before.local_commissioning_group, r_after.local_commissioning_group)
            as local_commissioning_group
    from gp_months as g
    asof left join register as r_before
        on g.practice_code = r_before.practice_code and g.month >= r_before.release_month
    asof left join register as r_after
        on g.practice_code = r_after.practice_code and g.month <= r_after.release_month
),

diabetes as (
    select
        month,
        practice_code,
        sum(items) as glucose_lowering_items,
        count(distinct substance) as glucose_lowering_drugs,
        coalesce(
            sum(items) filter (where lower(substance) = lower('{{ var("target_generic_name") }}')),
            0
        ) as target_drug_items,
        string_agg(distinct target_brand, ', ' order by target_brand) as target_drug_brands
    from {{ ref('stg_ni__diabetes_items') }}
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
    cast(null as varchar) as nhs_area_code,
    w.local_commissioning_group as nhs_area_name,
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
