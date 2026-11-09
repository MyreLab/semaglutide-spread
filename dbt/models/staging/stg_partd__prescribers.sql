-- One row per prescriber x year, typed and renamed: address, specialty and overall volume.
-- Prescriber names are dropped: the analysis never needs them.

select
    cast(year as smallint) as year,
    Prscrbr_NPI as npi,
    case Prscrbr_Ent_Cd when 'I' then 'individual' when 'O' then 'organization' end as entity_type,
    Prscrbr_Crdntls as credentials,
    Prscrbr_Type as prescriber_type,
    Prscrbr_Type_Src as prescriber_type_source,
    Prscrbr_St1 as street_1,
    Prscrbr_St2 as street_2,
    Prscrbr_City as city,
    Prscrbr_State_Abrvtn as state,
    Prscrbr_State_FIPS as state_fips,
    Prscrbr_Zip5 as zip5,
    Prscrbr_Cntry as country,
    Prscrbr_RUCA as ruca_code,
    Prscrbr_RUCA_Desc as ruca_description,
    cast(Tot_Clms as integer) as total_claims,
    cast(Tot_Drug_Cst as decimal(14, 2)) as total_drug_cost,
    cast(Tot_Benes as integer) as total_beneficiaries
from {{ source('partd', 'by_provider') }}
