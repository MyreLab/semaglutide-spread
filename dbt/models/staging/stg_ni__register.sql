-- Northern Ireland's GP practice reference files: one row per practice x release.

select
    cast(year_month || '-01' as date) as release_month,
    practice_code,
    practice_name,
    nullif(
        regexp_replace(
            upper(replace(postcode, ' ', '')), '^([A-Z]{1,2}[0-9][A-Z0-9]?)([0-9][A-Z]{2})$', '\1 \2'
        ),
        ''
    ) as postcode,
    nullif(lcg, 'NULL') as local_commissioning_group,
    -- Some files write "NULL" for missing values, and thousands separators ("6,806").
    cast(nullif(nullif(replace(registered_patients, ',', ''), ''), 'NULL') as integer)
        as registered_patients
from {{ source('ni', 'register') }}
