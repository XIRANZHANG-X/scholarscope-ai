-- The ten most common venues for each of the three main publication types.
--
-- `works_in_type` and `works_without_source` put the list in proportion: OpenAlex records no
-- venue at all for most conference papers, so their top ten covers only a minority of them.
WITH per_source AS (
    SELECT w.type, s.display_name AS source_name, s.type AS source_type, count(*) AS works
    FROM core.works w
    JOIN core.sources s USING (source_id)
    WHERE w.type IN ('preprint', 'conference-paper', 'article')
    GROUP BY w.type, s.display_name, s.type
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY type ORDER BY works DESC, source_name) AS rank_in_type
    FROM per_source
),
totals AS (
    SELECT type,
           count(*)                                    AS works_in_type,
           count(*) FILTER (WHERE source_id IS NULL)   AS works_without_source
    FROM core.works
    GROUP BY type
)
SELECT r.type, r.rank_in_type, r.source_name, r.source_type, r.works,
       round(100.0 * r.works / t.works_in_type, 1) AS share_of_type_pct,
       t.works_in_type, t.works_without_source
FROM ranked r
JOIN totals t USING (type)
WHERE r.rank_in_type <= 10
ORDER BY r.type, r.rank_in_type;
