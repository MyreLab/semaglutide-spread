-- Row counts between layers: each year, the number of prescribers of the target drug in the
-- staging model, in population_at_risk, and in int_prescriber_years must agree. A join that
-- drops or duplicates prescribers fails here.

with staging as (
    select year, count(distinct npi) as prescribers
    from {{ ref('stg_partd__prescriber_drugs') }}
    where generic_name = '{{ var("target_generic_name") }}'
    group by year
),

at_risk as (
    select year, count(*) as prescribers
    from {{ ref('us_population_at_risk') }}
    where prescribed_target_drug
    group by year
),

placed as (
    select p.year, count(*) as prescribers
    from {{ ref('us_population_at_risk') }} as p
    inner join {{ ref('int_prescriber_years') }} as y using (year, npi)
    where p.prescribed_target_drug
    group by p.year
)

select s.year, s.prescribers as staging, r.prescribers as at_risk, l.prescribers as placed
from staging as s
full join at_risk as r using (year)
full join placed as l using (year)
where s.prescribers is distinct from r.prescribers
    or s.prescribers is distinct from l.prescribers
