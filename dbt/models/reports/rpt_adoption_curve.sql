-- The epidemic curve: one row per year.
--
-- Population at risk (main analysis):
--   at_risk           prescribers in the population at risk this year
--   susceptible       of those, not yet adopted at the start of the year
--   new_adopters      adopted this year (all are in this year's population at risk)
--   incidence_pct     new_adopters / susceptible: the share of "susceptible" prescribers who
--                     adopted this year
--   adopted_at_risk   prescribers at risk this year who have adopted, this year or earlier
--   prevalence_pct    adopted_at_risk / at_risk: the share of this year's population at risk
--                     that has ever adopted
--   cumulative_adopters  everyone who has adopted so far, wherever they are now
--
-- All prescribers (sensitivity analysis): the same share, but out of every Part D prescriber
-- that year, whatever they prescribe.

with prescriber_years as (
    select
        y.year,
        y.npi,
        p.npi is not null as at_risk,
        a.adoption_year
    from {{ ref('int_prescriber_years') }} as y
    left join {{ ref('population_at_risk') }} as p using (year, npi)
    left join {{ ref('adopters') }} as a using (npi)
),

by_year as (
    select
        year,
        count(*) as all_prescribers,
        count(*) filter (where at_risk) as at_risk,
        count(*) filter (
            where at_risk and (adoption_year is null or adoption_year >= year)
        ) as susceptible,
        count(*) filter (where adoption_year = year) as new_adopters,
        count(*) filter (where at_risk and adoption_year <= year) as adopted_at_risk,
        count(*) filter (where adoption_year <= year) as adopted_all_prescribers
    from prescriber_years
    group by year
)

select
    year,
    at_risk,
    susceptible,
    new_adopters,
    round(100.0 * new_adopters / susceptible, 2) as incidence_pct,
    adopted_at_risk,
    round(100.0 * adopted_at_risk / at_risk, 2) as prevalence_pct,
    sum(new_adopters) over (order by year) as cumulative_adopters,
    all_prescribers,
    adopted_all_prescribers,
    round(100.0 * adopted_all_prescribers / all_prescribers, 2) as prevalence_all_prescribers_pct
from by_year
order by year
