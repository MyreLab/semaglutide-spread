-- One row per UK GP practice x month, all nations stacked with the same columns: where the
-- practice is, its NHS area (an integrated care board in England, a health board in
-- Scotland, a local commissioning group in Northern Ireland), and its items of all drugs, glucose-lowering drugs and the target drug.
-- Practice codes are only unique within a nation, so (nation, practice_code) is the key.

{% set columns %}
    month,
    practice_code,
    practice_name,
    postcode,
    postcode_status,
    latitude,
    longitude,
    region,
    nhs_area_code,
    nhs_area_name,
    local_authority_code,
    rural_urban_2021,
    list_size,
    all_items,
    glucose_lowering_items,
    glucose_lowering_drugs,
    target_drug_items,
    target_drug_brands
{% endset %}

select 'England' as nation, {{ columns }}
from (
    select
        *,
        icb_code as nhs_area_code,
        icb_name as nhs_area_name,
        cast(null as integer) as list_size  -- not in the English data
    from {{ ref('int_epd_practice_months') }}
    where is_gp_practice  -- leave out walk-in centres, community teams, prisons, etc.
)

union all

select 'Scotland' as nation, {{ columns }}
from {{ ref('int_phs_practice_months') }}

union all

select 'Northern Ireland' as nation, {{ columns }}
from {{ ref('int_ni_practice_months') }}
