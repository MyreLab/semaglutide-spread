-- The population at risk (the denominator): one row per prescriber x year in which the
-- prescriber had 11+ Part D claims for at least one glucose-lowering drug (WHO ATC A10, the
-- `included` rows of the glucose_lowering_drugs seed). The target drug counts, so every
-- adopter is in the population at risk in the year they adopt.
--
-- "11+ claims for at least one drug" is what the data can show: CMS leaves out any
-- prescriber x drug row with fewer than 11 claims, so a prescriber with 6 claims each for two
-- drugs is not visible here even though the total is 12.

with glucose_lowering_rows as (
    select
        d.year,
        d.npi,
        d.generic_name,
        d.brand_name,
        d.total_claims
    from {{ ref('stg_partd__prescriber_drugs') }} as d
    inner join {{ ref('glucose_lowering_drugs') }} as g using (generic_name)
    where g.included
),

per_prescriber_year as (
    select
        year,
        npi,
        sum(total_claims) as glucose_lowering_claims,
        count(distinct generic_name) as glucose_lowering_drugs,
        coalesce(
            sum(total_claims) filter (where generic_name = '{{ var("target_generic_name") }}'),
            0
        ) as target_drug_claims,
        string_agg(distinct brand_name, ', ' order by brand_name)
            filter (where generic_name = '{{ var("target_generic_name") }}') as target_drug_brands
    from glucose_lowering_rows
    group by year, npi
)

select
    r.year,
    r.npi,
    r.glucose_lowering_claims,
    r.glucose_lowering_drugs,
    r.target_drug_claims,
    r.target_drug_brands,
    r.target_drug_claims > 0 as prescribed_target_drug,
    y.entity_type,
    y.prescriber_type,
    y.specialty,
    y.specialty_group,
    y.ruca_code,
    y.location_method,
    y.latitude,
    y.longitude,
    y.county_fips,
    y.total_claims as all_drug_claims
from per_prescriber_year as r
left join {{ ref('int_prescriber_years') }} as y using (year, npi)
