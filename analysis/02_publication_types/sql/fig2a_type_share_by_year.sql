-- Publication-type mix of the RAG corpus by year, 2023-2026 (earlier years have under 20 papers).
-- Year-only dates are fine at this grain, so all papers are counted.
SELECT publication_year                                                            AS year,
       type,
       count(*)                                                                    AS works,
       sum(count(*)) OVER (PARTITION BY publication_year)                          AS works_in_year,
       round(100.0 * count(*) / sum(count(*)) OVER (PARTITION BY publication_year), 1) AS share_pct
FROM core.works
WHERE publication_year >= 2023
GROUP BY publication_year, type
ORDER BY year, type;
