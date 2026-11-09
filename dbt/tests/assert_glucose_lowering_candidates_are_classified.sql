-- Every Part D generic name that looks like a diabetes drug or supply must be listed in the
-- glucose_lowering_drugs seed, either included or excluded with a reason. This catches new
-- drugs (or renamed ones) when CMS publishes a new year, instead of silently leaving them out
-- of the population at risk.

with candidates as (
    select distinct generic_name
    from {{ ref('stg_partd__prescriber_drugs') }}
    where regexp_matches(
        lower(generic_name),
        'insulin|metformin|glipiz|glimepir|glybur|gliclaz|tolbut|chlorprop|tolaz|pioglit|rosiglit'
        || '|sitaglip|saxaglip|linaglip|alogli|exenat|liraglut|dulaglut|lixisen|semaglut|albiglut'
        || '|tirzepat|retatrut|canaglif|dapaglif|empaglif|ertuglif|bexaglif|sotaglif|acarbose'
        || '|miglitol|repaglin|nateglin|pramlint|bromocrip|colesevel'
    )
)

select c.generic_name
from candidates as c
left join {{ ref('glucose_lowering_drugs') }} as s using (generic_name)
where s.generic_name is null
