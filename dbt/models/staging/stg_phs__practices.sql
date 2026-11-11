-- Scotland: one row per practice code x month, with total items of all drugs. Includes codes
-- that are not GP practices (hospitals, unallocated prescribing, e.g. 99996); only codes in
-- the GP practice register are kept downstream.

select
    cast(year_month || '-01' as date) as month,
    practice_code,
    cast(cast(items as double) as integer) as items
from {{ source('phs', 'practices') }}
