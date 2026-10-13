-- Which sub-directions appear together in the same paper.
--
-- lift = P(A and B) / (P(A) * P(B)), over the papers with an abstract.
--   1.0  the two appear together exactly as often as chance would give
--   2.0  twice as often as chance        0.5  half as often
-- One row per ordered pair (A,B and B,A), so the result pivots straight into a square matrix.
-- A pair that never co-occurs has no row. Matching is on title or abstract.
-- See fig1_subfield_sizes.sql for how :rules works.
WITH rules AS (
    SELECT *
    FROM jsonb_to_recordset(CAST(:rules AS jsonb)) AS r(subfield text, label text, kind text, pattern text)
),
base AS (
    SELECT work_id, title || ' ' || abstract AS body
    FROM core.works
    WHERE abstract IS NOT NULL
),
tags AS (
    SELECT b.work_id, r.subfield, r.label
    FROM base b
    JOIN rules r ON b.body ~* r.pattern
),
sizes AS (
    SELECT subfield, count(*) AS papers FROM tags GROUP BY subfield
),
total AS (
    SELECT count(*) AS papers FROM base
)
SELECT a.subfield      AS subfield_a,
       a.label         AS label_a,
       b.subfield      AS subfield_b,
       b.label         AS label_b,
       count(*)        AS papers_both,
       sa.papers       AS papers_a,
       sb.papers       AS papers_b,
       t.papers        AS papers_with_abstract,
       round(100.0 * count(*) / sa.papers, 1)                                    AS share_of_a_pct,
       round(count(*)::numeric * t.papers / (sa.papers::numeric * sb.papers), 3) AS lift
FROM tags a
JOIN tags b ON b.work_id = a.work_id AND b.subfield <> a.subfield
JOIN sizes sa ON sa.subfield = a.subfield
JOIN sizes sb ON sb.subfield = b.subfield
CROSS JOIN total t
GROUP BY a.subfield, a.label, b.subfield, b.label, sa.papers, sb.papers, t.papers
ORDER BY subfield_a, lift DESC;
