-- Scotland's GP practice register: one row per practice x quarter.

select
    cast(quarter || '-01' as date) as quarter,
    practice_code,
    practice_name,
    cast(nullif(list_size, '') as integer) as list_size,
    nullif(
        regexp_replace(
            upper(replace(postcode, ' ', '')), '^([A-Z]{1,2}[0-9][A-Z0-9]?)([0-9][A-Z]{2})$', '\1 \2'
        ),
        ''
    ) as postcode,
    practice_type,
    health_board as health_board_code
from {{ source('phs', 'register') }}
