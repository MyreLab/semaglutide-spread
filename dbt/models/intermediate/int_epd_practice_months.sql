-- One row per English prescribing organisation x month (unidentified prescriptions left out):
-- whether it is a GP practice, where it is, and how much it prescribed of all drugs, of
-- glucose-lowering drugs and of the target drug. Only GP practices go on to the UK tables.
--
-- Incremental: England publishes a month at a time, so a normal build only reprocesses the
-- latest months (see recent_months below) and keeps the rest. Run with --full-refresh after a
-- change to this SQL or to a lookup it joins (the ODS practice list, the postcode lookup),
-- since those change the older months too.

{{
    config(
        materialized='incremental',
        unique_key=['month', 'practice_code'],
        incremental_strategy='delete+insert',
        on_schema_change='fail',
    )
}}

{#- Months to reprocess on an incremental build: the last 3 already in the table, in case a
    month is republished, plus anything newer. -#}
{% set recent_months %}
    {% if is_incremental() %}
        where month >= (select max(month) - interval 2 month from {{ this }})
    {% endif %}
{% endset %}

with practices as (
    select *
    from {{ ref('stg_epd__practices') }}
    {{ recent_months }}
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
    from {{ ref('stg_epd__diabetes_items') }}
    {{ recent_months }}
    group by month, practice_code
)

select
    p.month,
    p.practice_code,
    p.practice_name,
    p.postcode,
    p.practice_code in (select o.practice_code from {{ ref('stg_ods__gp_practices') }} as o)
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
from practices as p
left join diabetes as d using (month, practice_code)
left join {{ ref('stg_postcodes') }} as l using (postcode)
where not p.is_unidentified
