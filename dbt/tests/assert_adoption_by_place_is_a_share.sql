-- In every place, adopters are a subset of the population at risk.

select 'US' as country, year::varchar as period, place_type, place, adopted, at_risk
from {{ ref('rpt_us_adoption_by_place') }}
where adopted > at_risk

union all

select 'UK', month::varchar, place_type, place, adopted, at_risk
from {{ ref('rpt_uk_adoption_by_place') }}
where adopted > at_risk
