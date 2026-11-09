-- Every prescriber-year in the "by Provider and Drug" file must also appear in the
-- "by Provider" file, which is where locations come from.

select distinct d.year, d.npi
from {{ ref('stg_partd__prescriber_drugs') }} as d
anti join {{ ref('stg_partd__prescribers') }} as p using (year, npi)
