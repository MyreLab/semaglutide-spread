-- Northern Ireland: one row per practice x substance x month (x product, for the target drug).
-- Northern Ireland's BNF codes don't follow the standard format, so drugs are identified by
-- substance name (the dm+d "VTM"), e.g. "Semaglutide", "Metformin".

select
    cast(year_month || '-01' as date) as month,
    practice_code,
    substance,
    target_presentation,
    case
        when target_presentation is null then null
        when target_presentation ilike 'Ozempic%' then 'Ozempic'
        when target_presentation ilike 'Rybelsus%' then 'Rybelsus'
        when target_presentation ilike 'Wegovy%' then 'Wegovy'
        else 'Generic name'
    end as target_brand,
    cast(cast(items as double) as integer) as items,
    cast(quantity as double) as quantity,
    cast(gross_cost as double) as gross_cost,
    cast(actual_cost as double) as actual_cost
from {{ source('ni', 'diabetes') }}
