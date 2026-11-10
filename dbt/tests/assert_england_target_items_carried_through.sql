-- England: each month's target-drug items are the same in staging and in the population at
-- risk (minus unidentified prescriptions), so no join lost or duplicated rows.

with staging as (
    select d.month, sum(d.items) as items
    from {{ ref('stg_epd__diabetes_items') }} as d
    inner join {{ ref('stg_epd__practices') }} as p using (month, practice_code)
    where d.bnf_chemical_code = '{{ var("target_bnf_chemical_code") }}' and not p.is_unidentified
    group by d.month
),

at_risk as (
    select month, sum(target_drug_items) as items
    from {{ ref('uk_population_at_risk') }}
    where nation = 'England'
    group by month
)

select s.month, s.items as staging, r.items as at_risk
from staging as s
full join at_risk as r using (month)
where coalesce(s.items, 0) != coalesce(r.items, 0)
