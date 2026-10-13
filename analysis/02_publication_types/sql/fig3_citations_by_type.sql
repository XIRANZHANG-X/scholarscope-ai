-- Citations by publication type, within one publication year at a time.
--
-- Older papers have had longer to be cited, so types are only compared inside the same cohort
-- (papers published in 2024, and papers published in 2025). Citation counts are as of the
-- collection date, 2026-09-22. Reviews are kept in the table but are too few to chart (5 and 22).
SELECT publication_year                                                          AS cohort_year,
       type,
       count(*)                                                                  AS works,
       round(100.0 * count(*) FILTER (WHERE cited_by_count > 0) / count(*), 1)   AS cited_share_pct,
       round(avg(cited_by_count), 2)                                             AS mean_citations,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY cited_by_count)::numeric, 1) AS median_citations,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY cited_by_count)::numeric, 1) AS p90_citations,
       max(cited_by_count)                                                       AS max_citations
FROM core.works
WHERE publication_year IN (2024, 2025)
GROUP BY publication_year, type
ORDER BY cohort_year, type;
