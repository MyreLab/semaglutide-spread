-- One row per prescriber x year: who they are (specialty), where they are (location), and how
-- much they prescribe overall. The prescriber dimension used by the adoption models.

select
    p.year,
    p.npi,
    p.entity_type,
    p.prescriber_type,
    p.prescriber_type_source,
    coalesce(s.specialty, 'Unknown') as specialty,
    coalesce(s.specialty_group, 'Organization or unknown') as specialty_group,
    p.ruca_code,
    l.location_method,
    l.latitude,
    l.longitude,
    l.county_fips,
    p.total_claims
from {{ ref('stg_partd__prescribers') }} as p
left join {{ ref('specialty_groups') }} as s using (prescriber_type)
left join {{ ref('int_prescriber_locations') }} as l using (year, npi)
