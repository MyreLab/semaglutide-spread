-- Each split of the population at risk by place adds up to the whole: no prescriber or
-- practice is lost or counted twice by the state, area or rurality joins.

with us_totals as (
    select year, count(*) as at_risk
    from {{ ref('us_population_at_risk') }}
    group by year
),

us_splits as (
    select year, place_type, sum(at_risk) as at_risk
    from {{ ref('rpt_us_adoption_by_place') }}
    group by year, place_type
),

uk_totals as (
    select
        month,
        count(*) as at_risk,
        count(*) filter (where nation in ('England', 'Wales')) as at_risk_england_wales
    from {{ ref('uk_population_at_risk') }}
    group by month
),

uk_splits as (
    select month, place_type, sum(at_risk) as at_risk
    from {{ ref('rpt_uk_adoption_by_place') }}
    group by month, place_type
)

select 'US ' || s.place_type as split, s.year::varchar as period, s.at_risk, t.at_risk as total
from us_splits as s
inner join us_totals as t using (year)
where s.at_risk <> t.at_risk

union all

select 'UK ' || s.place_type, s.month::varchar, s.at_risk,
    case when s.place_type = 'rurality' then t.at_risk_england_wales else t.at_risk end
from uk_splits as s
inner join uk_totals as t using (month)
where s.at_risk <> case when s.place_type = 'rurality' then t.at_risk_england_wales else t.at_risk end
