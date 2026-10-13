-- Publication-type mix of the four themes (computer science, 2019-01-01 .. 2026-09-22), so the
-- RAG mix can be read against its neighbours. Counts come from meta.recall_probes; `latest`
-- keeps the most recent probe of each phrase (the RAG phrase was probed twice).
--
-- Only the four types the corpus was built from are compared; OpenAlex also returns
-- dissertations, book chapters and so on for these searches, and they are dropped here.
-- LLM, Agents and Foundation models add up several phrases (upper bounds); the mix is a share,
-- so double counting matters less than in the totals.
WITH latest AS (
    SELECT DISTINCT ON (query_key, field_scope, dimension, bucket)
           query_key, theme, field_scope, dimension, bucket, works_count
    FROM meta.recall_probes
    ORDER BY query_key, field_scope, dimension, bucket, probed_at DESC
),
by_type AS (
    SELECT theme, bucket AS type, sum(works_count) AS works
    FROM latest
    WHERE dimension = 'type' AND field_scope = 'cs'
      AND bucket IN ('preprint', 'conference-paper', 'article', 'review')
    GROUP BY theme, bucket
)
SELECT theme, type, works,
       sum(works) OVER (PARTITION BY theme)                             AS works_in_theme,
       round(100.0 * works / sum(works) OVER (PARTITION BY theme), 1)   AS share_pct
FROM by_type
ORDER BY theme, type;
