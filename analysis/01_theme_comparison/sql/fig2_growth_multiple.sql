-- Year-over-year growth multiple per theme (computer science): this year's papers / last year's.
-- 2.0 means the theme doubled. Only complete years are compared, so 2026 (to 22 Sep) is left out.
WITH latest AS (
    SELECT DISTINCT ON (query_key, field_scope, dimension, bucket)
           query_key, theme, field_scope, dimension, bucket, works_count
    FROM meta.recall_probes
    ORDER BY query_key, field_scope, dimension, bucket, probed_at DESC
),
yearly AS (
    SELECT theme, bucket::int AS year, sum(works_count) AS works
    FROM latest
    WHERE dimension = 'publication_year' AND field_scope = 'cs'
    GROUP BY theme, bucket
),
with_previous AS (
    SELECT theme, year, works,
           lag(works) OVER (PARTITION BY theme ORDER BY year) AS works_previous_year
    FROM yearly
)
SELECT theme, year, works, works_previous_year,
       round(works::numeric / works_previous_year, 2) AS growth_multiple
FROM with_previous
WHERE year BETWEEN 2022 AND 2025
ORDER BY theme, year;
