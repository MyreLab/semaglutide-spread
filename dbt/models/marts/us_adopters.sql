-- The adopters (the numerator): one row per prescriber who ever prescribed the target drug,
-- with the first year they did ("adoption year"), and who they were and where in that year.
--
-- Adoption = the first data year with 11+ Part D claims for the target drug, any brand. Part D
-- only shows a prescriber x brand row with 11+ claims, so "has a row" means "11+ claims for at
-- least one brand". Specialty and location are taken from the adoption year: prescribers move
-- and change specialty, and the adoption year is when the "infection" happened.
--
-- Adopters are drawn from us_population_at_risk, so the numerator is a subset of the denominator
-- by construction (a test checks it anyway).

with first_year as (
    select
        npi,
        min(year) as adoption_year
    from {{ ref('us_population_at_risk') }}
    where prescribed_target_drug
    group by npi
)

select
    f.npi,
    f.adoption_year,
    p.target_drug_brands as adoption_brands,
    p.target_drug_claims as adoption_year_claims,
    p.glucose_lowering_claims as adoption_year_glucose_lowering_claims,
    p.entity_type,
    p.prescriber_type,
    p.specialty,
    p.specialty_group,
    p.ruca_code,
    p.location_method,
    p.latitude,
    p.longitude,
    p.county_fips
from first_year as f
inner join {{ ref('us_population_at_risk') }} as p
    on f.npi = p.npi and f.adoption_year = p.year
