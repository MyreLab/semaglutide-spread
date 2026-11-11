-- The numerator must be a subset of the denominator: every adopter is in the population at
-- risk in their adoption year.

select a.npi, a.adoption_year
from {{ ref('us_adopters') }} as a
left join {{ ref('us_population_at_risk') }} as p
    on a.npi = p.npi and a.adoption_year = p.year
where p.npi is null
