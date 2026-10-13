-- Share of each year's RAG papers that mention each sub-direction, 2023-2026.
-- The denominator is that year's papers with an abstract. 2023 has only about a hundred, so
-- the figure starts at 2024; the 2023 rows stay in the CSV.
-- See fig1_subfield_sizes.sql for how :rules and the two kinds of match work.
WITH rules AS (
    SELECT *
    FROM jsonb_to_recordset(CAST(:rules AS jsonb)) AS r(subfield text, label text, kind text, pattern text)
),
base AS (
    SELECT work_id, publication_year, title, title || ' ' || abstract AS body
    FROM core.works
    WHERE abstract IS NOT NULL AND publication_year >= 2023
)
SELECT b.publication_year                                                         AS year,
       r.subfield, r.label, r.kind,
       count(*) FILTER (WHERE b.body  ~* r.pattern)                               AS papers,
       count(*) FILTER (WHERE b.title ~* r.pattern)                               AS papers_title,
       count(*)                                                                   AS papers_with_abstract,
       round(100.0 * count(*) FILTER (WHERE b.body  ~* r.pattern) / count(*), 1)  AS share_pct,
       round(100.0 * count(*) FILTER (WHERE b.title ~* r.pattern) / count(*), 1)  AS share_title_pct
FROM rules r
CROSS JOIN base b
GROUP BY b.publication_year, r.subfield, r.label, r.kind
ORDER BY r.subfield, year;
