-- Every English and Welsh prescribing cost centre that the NHS Organisation Data Service
-- lists as a GP practice, open or closed.

select
    OrgId as practice_code,
    Name as practice_name,
    Status as status,
    PostCode as postcode,
    cast(LastChangeDate as date) as last_change_date
from {{ source('ods', 'gp_practices') }}
