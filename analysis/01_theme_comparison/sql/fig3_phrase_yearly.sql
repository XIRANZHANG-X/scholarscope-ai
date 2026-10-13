-- Papers per year for each of the eight search phrases on its own (computer science and all
-- fields). Unlike the theme totals in fig1, every number here is an exact OpenAlex count.
-- The phrase text is read back out of the stored OpenAlex filter.
WITH latest AS (
    SELECT DISTINCT ON (query_key, field_scope, dimension, bucket)
           query_key, theme, field_scope, dimension, bucket, works_count, filter
    FROM meta.recall_probes
    ORDER BY query_key, field_scope, dimension, bucket, probed_at DESC
)
SELECT theme,
       query_key,
       max(substring(filter FROM 'search:"([^"]+)"'))        AS phrase,
       bucket::int                                           AS year,
       sum(works_count) FILTER (WHERE field_scope = 'cs')    AS works_cs,
       sum(works_count) FILTER (WHERE field_scope = 'all')   AS works_all
FROM latest
WHERE dimension = 'publication_year'
GROUP BY theme, query_key, bucket
ORDER BY theme, query_key, year;
