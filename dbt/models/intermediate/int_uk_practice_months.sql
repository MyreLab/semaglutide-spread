-- One row per UK GP practice x month, all nations stacked, with the same columns:
-- where the practice is, and its items of all drugs, glucose-lowering drugs and the target
-- drug. Practice codes are only unique within a nation, so (nation, practice_code) is the key.

select 'England' as nation, *
from {{ ref('int_epd_practice_months') }}
