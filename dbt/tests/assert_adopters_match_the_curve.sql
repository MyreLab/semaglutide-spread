-- The curve's yearly new adopters must add up to the adopters table, and no year can have more
-- new adopters than susceptible prescribers.

select 'total' as check_name, year
from {{ ref('rpt_adoption_curve') }}
where year = (select max(year) from {{ ref('rpt_adoption_curve') }})
    and cumulative_adopters != (select count(*) from {{ ref('adopters') }})

union all

select 'new_adopters_above_susceptible' as check_name, year
from {{ ref('rpt_adoption_curve') }}
where new_adopters > susceptible
