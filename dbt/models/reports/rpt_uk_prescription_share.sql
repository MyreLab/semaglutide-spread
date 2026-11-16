-- Semaglutide's share of glucose-lowering prescription items from UK GP practices, one row
-- per nation x calendar year.
--
-- This is about volume, not adoption. Glucose-lowering = BNF 6.1.1 and 6.1.2, as in the
-- population at risk; every GP practice with any glucose-lowering item counts. A year can be
-- partial (the first year of data, or the latest): `months` says how many months it has.

select
    nation,
    cast(year(month) as smallint) as year,
    count(distinct month) as months,
    cast(sum(glucose_lowering_items) as bigint) as glucose_lowering_items,
    cast(sum(target_drug_items) as bigint) as target_drug_items,
    round(100.0 * sum(target_drug_items) / sum(glucose_lowering_items), 2) as target_share_pct
from {{ ref('uk_population_at_risk') }}
group by nation, year(month)
order by nation, year
