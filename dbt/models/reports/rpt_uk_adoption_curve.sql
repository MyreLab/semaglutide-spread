-- The UK epidemic curves: one row per nation x month, for both adoption measures (see
-- uk_adopters).
-- Same definitions as the US curve: susceptible = at risk this month and not yet adopted;
-- incidence = new adopters / susceptible; prevalence = ever adopted / this month's at-risk.

with practice_months as (
    select
        p.nation,
        p.month,
        p.practice_code,
        a.adoption_month,
        a.matched_adoption_month
    from {{ ref('uk_population_at_risk') }} as p
    left join {{ ref('uk_adopters') }} as a using (nation, practice_code)
),

by_month as (
    select
        nation,
        month,
        count(*) as at_risk,
        count(*) filter (where adoption_month is null or adoption_month >= month) as susceptible,
        count(*) filter (where adoption_month = month) as new_adopters,
        count(*) filter (where adoption_month <= month) as adopted_at_risk,
        count(*) filter (
            where matched_adoption_month is null or matched_adoption_month >= month
        ) as matched_susceptible,
        count(*) filter (where matched_adoption_month = month) as matched_new_adopters,
        count(*) filter (where matched_adoption_month <= month) as matched_adopted_at_risk
    from practice_months
    group by nation, month
)

select
    nation,
    month,
    at_risk,
    susceptible,
    new_adopters,
    -- null once no practice is left to adopt (every practice has adopted)
    round(100.0 * new_adopters / nullif(susceptible, 0), 2) as incidence_pct,
    adopted_at_risk,
    round(100.0 * adopted_at_risk / at_risk, 2) as prevalence_pct,
    matched_susceptible,
    matched_new_adopters,
    round(100.0 * matched_new_adopters / nullif(matched_susceptible, 0), 2) as matched_incidence_pct,
    matched_adopted_at_risk,
    round(100.0 * matched_adopted_at_risk / at_risk, 2) as matched_prevalence_pct
from by_month
order by nation, month
