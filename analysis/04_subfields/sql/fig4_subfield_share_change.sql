-- Which sub-directions gained or lost share between 2024 and 2026.
-- change_pp is in percentage points: share of the 2026 papers minus share of the 2024 papers
-- (papers with an abstract, matching on title or abstract). The *_title columns repeat the same
-- for matches in the title only. 2026 runs to 22 September; a share is not affected by that the
-- way a count is.
-- See fig1_subfield_sizes.sql for how :rules works.
WITH rules AS (
    SELECT *
    FROM jsonb_to_recordset(CAST(:rules AS jsonb)) AS r(subfield text, label text, kind text, pattern text)
),
base AS (
    SELECT work_id, publication_year AS year, title, title || ' ' || abstract AS body
    FROM core.works
    WHERE abstract IS NOT NULL AND publication_year IN (2024, 2025, 2026)
),
shares AS (
    SELECT r.subfield, r.label, r.kind,
           100.0 * count(*) FILTER (WHERE b.year = 2024 AND b.body ~* r.pattern)  / count(*) FILTER (WHERE b.year = 2024) AS share_2024,
           100.0 * count(*) FILTER (WHERE b.year = 2025 AND b.body ~* r.pattern)  / count(*) FILTER (WHERE b.year = 2025) AS share_2025,
           100.0 * count(*) FILTER (WHERE b.year = 2026 AND b.body ~* r.pattern)  / count(*) FILTER (WHERE b.year = 2026) AS share_2026,
           100.0 * count(*) FILTER (WHERE b.year = 2024 AND b.title ~* r.pattern) / count(*) FILTER (WHERE b.year = 2024) AS share_title_2024,
           100.0 * count(*) FILTER (WHERE b.year = 2026 AND b.title ~* r.pattern) / count(*) FILTER (WHERE b.year = 2026) AS share_title_2026
    FROM rules r
    CROSS JOIN base b
    GROUP BY r.subfield, r.label, r.kind
)
SELECT subfield, label, kind,
       round(share_2024, 1)                          AS share_2024_pct,
       round(share_2025, 1)                          AS share_2025_pct,
       round(share_2026, 1)                          AS share_2026_pct,
       round(share_2026 - share_2024, 1)             AS change_pp,
       round(share_title_2024, 1)                    AS share_title_2024_pct,
       round(share_title_2026, 1)                    AS share_title_2026_pct,
       round(share_title_2026 - share_title_2024, 1) AS change_title_pp
FROM shares
ORDER BY change_pp DESC;
