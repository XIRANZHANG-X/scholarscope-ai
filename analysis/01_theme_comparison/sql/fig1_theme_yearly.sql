-- Papers per year for the four themes, computer science and all fields.
--
-- meta.recall_probes holds counts returned by OpenAlex, one row per probe run, search phrase,
-- field scope and year. Nothing is de-duplicated across runs, and the RAG phrase was probed
-- twice (it sits in both config/recall.toml and config/context.toml), so a plain sum() doubles
-- RAG. `latest` keeps only the most recent run of each phrase.
--
-- A theme is the sum of its phrases (see fig3 for the phrases one by one). A paper that matches
-- two phrases of one theme is counted twice, so LLM, Agents and Foundation Models are upper
-- bounds. RAG has a single phrase and is exact.
WITH latest AS (
    SELECT DISTINCT ON (query_key, field_scope, dimension, bucket)
           query_key, theme, field_scope, dimension, bucket, works_count
    FROM meta.recall_probes
    ORDER BY query_key, field_scope, dimension, bucket, probed_at DESC
)
SELECT theme,
       bucket::int                                           AS year,
       sum(works_count) FILTER (WHERE field_scope = 'cs')    AS works_cs,
       sum(works_count) FILTER (WHERE field_scope = 'all')   AS works_all,
       count(DISTINCT query_key)                             AS phrases
FROM latest
WHERE dimension = 'publication_year'
GROUP BY theme, bucket
ORDER BY theme, year;
