-- Wales: the monthly address file, one row per prescribing organisation x month. The first
-- address line holds the practice's name.

select
    cast(year_month || '-01' as date) as month,
    practice_code,
    any_value(practice_name) as practice_name,
    any_value(
        nullif(
            regexp_replace(
                upper(replace(postcode, ' ', '')),
                '^([A-Z]{1,2}[0-9][A-Z0-9]?)([0-9][A-Z]{2})$',
                '\1 \2'
            ),
            ''
        )
    ) as postcode
from {{ source('wales', 'register') }}
group by all
