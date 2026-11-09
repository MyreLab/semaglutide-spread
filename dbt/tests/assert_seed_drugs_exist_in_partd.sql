-- Every generic name in the glucose_lowering_drugs seed must appear in Part D exactly as
-- written. A typo in the seed would otherwise silently match nothing.

select s.generic_name
from {{ ref('glucose_lowering_drugs') }} as s
left join (
    select distinct generic_name from {{ ref('stg_partd__prescriber_drugs') }}
) as d using (generic_name)
where d.generic_name is null
