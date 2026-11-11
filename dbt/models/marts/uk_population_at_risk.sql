-- The UK population at risk (the denominator): one row per GP practice x month with at least
-- one item of a glucose-lowering drug (BNF 6.1.1 insulins or 6.1.2 antidiabetic drugs, the
-- target drug included). UK data publish every item, so there is no 11-item floor as in the
-- US data.

select *
from {{ ref('int_uk_practice_months') }}
where glucose_lowering_items > 0
