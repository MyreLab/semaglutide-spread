-- One row per prescribing organisation x month: every code that prescribed anything that
-- month. "Unidentified doctors" (code "-" or ending in 998) collect prescriptions that
-- couldn't be traced to a practice; they are flagged here and left out downstream.

select
    cast(year_month || '-01' as date) as month,
    practice_code,
    practice_name,
    address_1,
    address_2,
    address_3,
    address_4,
    -- Tidy to the standard "AB1 2CD" form, the same as the postcode lookup.
    nullif(
        regexp_replace(
            upper(replace(postcode, ' ', '')), '^([A-Z]{1,2}[0-9][A-Z0-9]?)([0-9][A-Z]{2})$', '\1 \2'
        ),
        '-'
    ) as postcode,
    cast(items as integer) as items,
    practice_code = '-' or practice_name = 'UNIDENTIFIED DOCTORS' as is_unidentified
from {{ source('epd', 'practices') }}
