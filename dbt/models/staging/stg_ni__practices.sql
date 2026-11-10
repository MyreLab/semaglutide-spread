-- Northern Ireland: one row per practice x month, with total items of all drugs.

select
    cast(year_month || '-01' as date) as month,
    practice_code,
    cast(cast(items as double) as integer) as items
from {{ source('ni', 'practices') }}
