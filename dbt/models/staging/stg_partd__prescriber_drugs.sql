-- One row per prescriber x drug x year, typed and renamed.
-- Prescriber names are dropped: the analysis never needs them. Beneficiary counts are null
-- where CMS suppressed them (fewer than 11 beneficiaries).

select
    cast(year as smallint) as year,
    Prscrbr_NPI as npi,
    Prscrbr_Type as prescriber_type,
    Prscrbr_Type_Src as prescriber_type_source,
    Brnd_Name as brand_name,
    Gnrc_Name as generic_name,
    cast(Tot_Clms as integer) as total_claims,
    cast(Tot_30day_Fills as double) as total_30day_fills,
    cast(Tot_Day_Suply as integer) as total_day_supply,
    cast(Tot_Drug_Cst as decimal(14, 2)) as total_drug_cost,
    cast(Tot_Benes as integer) as total_beneficiaries
from {{ source('partd', 'by_provider_and_drug') }}
