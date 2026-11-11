-- UK: every adopter is in the population at risk in its adoption month, and the matched
-- adoption month can't come before the first item.

select a.nation, a.practice_code, a.adoption_month, a.matched_adoption_month
from {{ ref('uk_adopters') }} as a
left join {{ ref('uk_population_at_risk') }} as p
    on a.nation = p.nation and a.practice_code = p.practice_code and a.adoption_month = p.month
where p.practice_code is null
    or a.matched_adoption_month < a.adoption_month
