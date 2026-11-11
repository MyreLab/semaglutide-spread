-- Semaglutide's share of glucose-lowering prescriptions in Medicare Part D, one row per year.
--
-- This is about volume, not adoption: every prescriber counts, not only the population at
-- risk. Volume is measured two ways:
--   claims         one per fill, whatever its length (a 90-day fill is 1 claim)
--   30-day fills   fills standardised to 30 days (a 90-day fill counts as 3)
-- A UK prescription item has no fixed length, but NHS GP prescriptions are commonly for 28
-- days, so 30-day fills are the closer match to UK items; both measures are kept.
-- CMS leaves out prescriber x drug rows with fewer than 11 claims, for every drug alike.

with glucose_lowering_rows as (
    select
        d.year,
        d.generic_name = '{{ var("target_generic_name") }}' as is_target,
        d.total_claims,
        d.total_30day_fills
    from {{ ref('stg_partd__prescriber_drugs') }} as d
    inner join {{ ref('glucose_lowering_drugs') }} as g using (generic_name)
    where g.included
),

by_year as (
    select
        year,
        sum(total_claims) as glucose_lowering_claims,
        coalesce(sum(total_claims) filter (where is_target), 0) as target_drug_claims,
        -- CMS publishes 30-day fills with decimals (a 45-day fill is 1.5)
        round(sum(total_30day_fills), 1) as glucose_lowering_30day_fills,
        round(coalesce(sum(total_30day_fills) filter (where is_target), 0), 1)
            as target_drug_30day_fills
    from glucose_lowering_rows
    group by year
)

select
    year,
    glucose_lowering_claims,
    target_drug_claims,
    round(100.0 * target_drug_claims / glucose_lowering_claims, 2) as target_share_claims_pct,
    glucose_lowering_30day_fills,
    target_drug_30day_fills,
    round(100.0 * target_drug_30day_fills / glucose_lowering_30day_fills, 2)
        as target_share_30day_fills_pct
from by_year
order by year
