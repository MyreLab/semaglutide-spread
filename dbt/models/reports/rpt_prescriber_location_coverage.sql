-- How every prescriber-year was placed on the map, by year: the match-rate report.

select
    year,
    location_method,
    coalesce(unplaced_reason, '') as unplaced_reason,
    count(*) as prescriber_years,
    round(100.0 * count(*) / sum(count(*)) over (partition by year), 3) as pct_of_year
from {{ ref('int_prescriber_locations') }}
group by year, location_method, unplaced_reason
order by year, prescriber_years desc
