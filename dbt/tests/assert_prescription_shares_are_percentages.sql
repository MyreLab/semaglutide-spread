-- Every share of prescriptions is a percentage, and a drug's prescriptions can't exceed the
-- glucose-lowering total they're part of.

select 'US claims' as measure, year::varchar as period, target_share_claims_pct as pct
from {{ ref('rpt_us_prescription_share') }}
where target_share_claims_pct not between 0 and 100

union all

select 'US 30-day fills', year::varchar, target_share_30day_fills_pct
from {{ ref('rpt_us_prescription_share') }}
where target_share_30day_fills_pct not between 0 and 100

union all

select 'UK items: ' || nation, year::varchar, target_share_pct
from {{ ref('rpt_uk_prescription_share') }}
where target_share_pct not between 0 and 100 or months not between 1 and 12
