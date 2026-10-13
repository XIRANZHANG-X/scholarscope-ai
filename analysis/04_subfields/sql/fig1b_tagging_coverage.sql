-- How much of the corpus the rules reach: one row of totals for the notes under the figures.
-- See fig1_subfield_sizes.sql for how :rules and the two kinds of match work.
WITH rules AS (
    SELECT *
    FROM jsonb_to_recordset(CAST(:rules AS jsonb)) AS r(subfield text, label text, kind text, pattern text)
),
base AS (
    SELECT work_id, title, title || ' ' || abstract AS body
    FROM core.works
    WHERE abstract IS NOT NULL
),
per_paper AS (
    SELECT b.work_id,
           count(*) FILTER (WHERE b.body  ~* r.pattern) AS tags,
           count(*) FILTER (WHERE b.title ~* r.pattern) AS title_tags
    FROM base b
    CROSS JOIN rules r
    GROUP BY b.work_id
)
SELECT count(*)                                                        AS papers_with_abstract,
       (SELECT count(*) FROM core.works WHERE abstract IS NULL)        AS papers_without_abstract,
       count(*) FILTER (WHERE tags > 0)                                AS papers_tagged,
       round(100.0 * count(*) FILTER (WHERE tags > 0) / count(*), 1)   AS tagged_pct,
       round(avg(tags), 2)                                             AS mean_tags_per_paper,
       count(*) FILTER (WHERE title_tags > 0)                          AS papers_tagged_by_title,
       round(100.0 * count(*) FILTER (WHERE title_tags > 0) / count(*), 1) AS tagged_by_title_pct
FROM per_paper;
