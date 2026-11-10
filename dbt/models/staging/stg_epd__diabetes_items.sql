-- One row per practice x BNF chemical substance x month (x semaglutide presentation), for
-- BNF 6.1.1 (insulins) and 6.1.2 (antidiabetic drugs). Items are prescription items: one
-- item is one drug on one prescription form, whatever the quantity.

select
    cast(year_month || '-01' as date) as month,
    practice_code,
    bnf_chemical_code,
    bnf_chemical_name,
    target_presentation,
    -- Brand of the target drug, from the product name. Prescriptions written by generic name
    -- ("Semaglutide 9mg tablets") don't say which brand was dispensed.
    case
        when target_presentation is null then null
        when target_presentation ilike 'Ozempic%' then 'Ozempic'
        when target_presentation ilike 'Rybelsus%' then 'Rybelsus'
        when target_presentation ilike 'Wegovy%' then 'Wegovy'
        else 'Generic name'
    end as target_brand,
    cast(items as integer) as items,
    cast(total_quantity as double) as total_quantity,
    cast(nic as double) as net_ingredient_cost,
    cast(actual_cost as double) as actual_cost
from {{ source('epd', 'diabetes') }}
