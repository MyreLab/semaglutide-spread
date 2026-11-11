-- Wales: one row per practice x BNF chemical substance x month (x product, for the target
-- drug). Wales uses the standard BNF codes, like England and Scotland.

select
    cast(year_month || '-01' as date) as month,
    practice_code,
    health_board as health_board_code,
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
    cast(quantity as double) as quantity,
    cast(nic as double) as net_ingredient_cost,
    cast(actual_cost as double) as actual_cost
from {{ source('wales', 'diabetes') }}
