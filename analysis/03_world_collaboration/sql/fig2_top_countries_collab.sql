-- The 15 countries with the most RAG papers, split into papers written within one country and
-- papers co-authored with at least one other country.
-- The last two columns repeat the corpus-wide figures on every row, for the figure's subtitle.
WITH per_work AS (
    SELECT work_id, count(*) AS n_countries
    FROM bridge.work_countries
    GROUP BY work_id
),
per_country AS (
    SELECT wc.country_code,
           count(*)                                      AS papers,
           count(*) FILTER (WHERE pw.n_countries = 1)    AS domestic_only,
           count(*) FILTER (WHERE pw.n_countries > 1)    AS international
    FROM bridge.work_countries wc
    JOIN per_work pw USING (work_id)
    GROUP BY wc.country_code
)
SELECT rank() OVER (ORDER BY pc.papers DESC)                         AS rank,
       pc.country_code,
       coalesce(p.name, pc.country_code)                             AS country,
       pc.papers,
       pc.domestic_only,
       pc.international,
       round(100.0 * pc.international / pc.papers, 1)                AS international_share_pct,
       (SELECT count(*) FROM per_work)                               AS all_papers_with_country,
       (SELECT count(*) FROM per_work WHERE n_countries > 1)         AS all_international_papers
FROM per_country pc
LEFT JOIN external.country_profiles p USING (country_code)
ORDER BY pc.papers DESC
LIMIT 15;
