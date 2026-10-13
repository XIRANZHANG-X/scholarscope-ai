-- RAG papers per 100 "large language model" papers, by year and field scope.
-- Both sides are single phrases, so both counts are exact. It is a ratio of two searches, not a
-- share: a RAG paper need not also say "large language model".
WITH latest AS (
    SELECT DISTINCT ON (query_key, field_scope, dimension, bucket)
           query_key, field_scope, dimension, bucket, works_count
    FROM meta.recall_probes
    ORDER BY query_key, field_scope, dimension, bucket, probed_at DESC
),
yearly AS (
    SELECT field_scope,
           bucket::int AS year,
           sum(works_count) FILTER (WHERE query_key = 'rag.retrieval_augmented_generation') AS rag_works,
           sum(works_count) FILTER (WHERE query_key = 'llm.large_language_model')           AS llm_works
    FROM latest
    WHERE dimension = 'publication_year'
    GROUP BY field_scope, bucket
)
SELECT field_scope, year, rag_works, llm_works,
       round(100.0 * rag_works / llm_works, 2) AS rag_per_100_llm
FROM yearly
WHERE year >= 2022
ORDER BY field_scope, year;
