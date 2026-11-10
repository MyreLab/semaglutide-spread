-- One row per English prescribing organisation x month (unidentified prescriptions left out):
-- whether it is a GP practice, where it is, and how much it prescribed of all drugs, of
-- glucose-lowering drugs and of the target drug. Only GP practices go on to the UK tables.

with diabetes as (
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
    from {{ ref('stg_epd__diabetes_items') }}
    group by month, practice_code
)

select
    p.month,
    p.practice_code,
    p.practice_name,
    p.postcode,
    p.practice_code in (select practice_code from {{ ref('stg_ods__gp_practices') }})
        as is_gp_practice,
    coalesce(l.lookup_status, 'not_looked_up') as postcode_status,
    l.latitude,
    l.longitude,
    l.country,
    l.region,
    l.icb_code,
    l.icb_name,
    l.local_authority_code,
    l.rural_urban_2021,
    p.items as all_items,
    coalesce(d.glucose_lowering_items, 0) as glucose_lowering_items,
    coalesce(d.glucose_lowering_drugs, 0) as glucose_lowering_drugs,
    coalesce(d.target_drug_items, 0) as target_drug_items,
    d.target_drug_brands
from {{ ref('stg_epd__practices') }} as p
left join diabetes as d using (month, practice_code)
left join {{ ref('stg_postcodes') }} as l using (postcode)
where not p.is_unidentified
