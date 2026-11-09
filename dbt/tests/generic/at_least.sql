{% test at_least(model, column_name, min_value) %}
-- Fails for every row where the column is below min_value (nulls are allowed).
select {{ column_name }}
from {{ model }}
where {{ column_name }} < {{ min_value }}
{% endtest %}
