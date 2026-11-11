-- Wales: one row per prescribing organisation x month, with its health board and total items.

select
    cast(year_month || '-01' as date) as month,
    practice_code,
    health_board as health_board_code,
    cast(cast(items as double) as integer) as items
from {{ source('wales', 'practices') }}
