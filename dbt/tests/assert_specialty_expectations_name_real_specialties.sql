-- Every specialty named in `specialty_expectations` must exist, in that group, in
-- `specialty_groups`: a typo would silently leave the specialty with its group's expectation.

select e.specialty_group, e.specialty
from {{ ref('specialty_expectations') }} as e
left join {{ ref('specialty_groups') }} as g
    on e.specialty_group = g.specialty_group and e.specialty = g.specialty
where e.specialty is not null and g.specialty is null
