-- Every prescriber type in either Part D file must be in the specialty_groups seed. A new label
-- in a future CMS release fails the build instead of silently landing in no group. A missing
-- type is allowed: it is grouped as "Organization or unknown".

with types as (
    select distinct prescriber_type from {{ ref('stg_partd__prescribers') }}
    union
    select distinct prescriber_type from {{ ref('stg_partd__prescriber_drugs') }}
)

select t.prescriber_type
from types as t
left join {{ ref('specialty_groups') }} as s using (prescriber_type)
where s.prescriber_type is null and t.prescriber_type is not null
