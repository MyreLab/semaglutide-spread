{% docs __overview__ %}
# semaglutide-spread

The dbt project behind [semaglutide-spread](https://github.com/MyreLab/semaglutide-spread), which tracks how semaglutide (Ozempic, Rybelsus, Wegovy) spread through Medicare Part D prescribers in the US and GP practices in the UK.

Models are layered as staging (type and rename each source), intermediate (locations, specialties, practice-months), marts (population at risk and adopters) and reports (the tables behind the charts). Open the lineage graph from the button at the bottom right.

This site is rebuilt by GitHub Actions on every push to `main`. CI doesn't have the real data, so it builds on the small made-up sample the tests use: models, columns, descriptions and tests are the real ones, but no real numbers appear here.
{% enddocs %}
