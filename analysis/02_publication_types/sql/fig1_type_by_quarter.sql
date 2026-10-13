-- RAG papers per quarter and publication type, from 2024 on.
--
-- 2,417 papers (15.2%) carry the date 1 January only because OpenAlex knows their year but not
-- their month. They would all land in the first quarter, so they are left out here and counted
-- separately in `year_only_date` (same value on every row of a year, for the figure's note).
WITH dated AS (
    SELECT w.work_id, w.type, w.publication_year, w.publication_date,
           to_char(w.publication_date, 'MM-DD') = '01-01' AS year_only
    FROM core.works w
    WHERE w.publication_date >= DATE '2024-01-01'
)
SELECT to_char(date_trunc('quarter', publication_date), 'YYYY"Q"Q')      AS quarter,
       type,
       count(*)                                                          AS works,
       (SELECT count(*) FROM dated y
         WHERE y.year_only AND y.publication_year = d.publication_year)   AS year_only_date
FROM dated d
WHERE NOT year_only
GROUP BY quarter, type, d.publication_year
ORDER BY quarter, type;
