-- US adopters by specialty, and whether that specialty would be expected to prescribe a
-- diabetes drug (seed `specialty_expectations`: a specialty's own row if it has one, else its
-- group's). One row per specialty group x specialty.
--
-- Adopters reached 11+ semaglutide claims in a year, so even the unexpected ones are not
-- one-off prescriptions.

with expectations as (
    select
        a.specialty_group,
        coalesce(a.specialty, 'Unknown') as specialty,
        a.adoption_year,
        coalesce(own.expectation, grp.expectation) as expectation,
        coalesce(own.reason, grp.reason) as reason
    from {{ ref('us_adopters') }} as a
    left join {{ ref('specialty_expectations') }} as own
        on a.specialty_group = own.specialty_group and a.specialty = own.specialty
    left join {{ ref('specialty_expectations') }} as grp
        on a.specialty_group = grp.specialty_group and grp.specialty is null
)

select
    specialty_group,
    specialty,
    expectation,
    reason,
    count(*) as adopters,
    min(adoption_year) as first_adoption_year
from expectations
group by all
order by adopters desc
