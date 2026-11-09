-- No more than 1% of a year's prescribers may be left off the map. Fails for any year above it.

select year, round(100.0 * avg((location_method = 'unplaced')::int), 3) as pct_unplaced
from {{ ref('int_prescriber_locations') }}
group by year
having avg((location_method = 'unplaced')::int) > 0.01
