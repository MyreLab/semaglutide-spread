-- No adoption year can be later than the latest year of data.

select npi, adoption_year
from {{ ref('adopters') }}
where adoption_year > (select max(year) from {{ ref('population_at_risk') }})
