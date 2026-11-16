-- The UK population at risk (the denominator): one row per GP practice x month with at least
-- one item of a glucose-lowering drug (BNF 6.1.1 insulins or 6.1.2 antidiabetic drugs, the
-- target drug included). UK data publish every item, so there is no 11-item floor as in the
-- US data.

select
    nation,
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
    cast(glucose_lowering_items as bigint) as glucose_lowering_items,
    glucose_lowering_drugs,
    cast(target_drug_items as bigint) as target_drug_items,
    target_drug_brands
from {{ ref('int_uk_practice_months') }}
where glucose_lowering_items > 0
