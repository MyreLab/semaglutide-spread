-- Scotland: one row per practice code x BNF chemical substance x month (x presentation, for
-- the target drug). Months are paid months: when the dispensing was paid for.

select
    cast(year_month || '-01' as date) as month,
    practice_code,
    bnf_chemical_code,
    target_presentation,
    case
        when target_presentation is null then null
        when target_presentation ilike 'Ozempic%' then 'Ozempic'
        when target_presentation ilike 'Rybelsus%' then 'Rybelsus'
        when target_presentation ilike 'Wegovy%' then 'Wegovy'
        else 'Generic name'
    end as target_brand,
    cast(cast(items as double) as integer) as items,
    cast(paid_quantity as double) as paid_quantity,
    cast(gross_ingredient_cost as double) as gross_ingredient_cost
from {{ source('phs', 'diabetes') }}
