-- Share of each year's RAG papers that involve each of the six largest producers, 2023-2026.
--
-- The denominator is the papers of that year that have any country (`papers_with_country`), not
-- all papers: 41% of the corpus has no country at all. Full counting, so the shares of one year
-- can add up to more than 100%. 2023 rests on very few papers; read it with its n.
WITH top AS (
    SELECT country_code, row_number() OVER (ORDER BY count(*) DESC) AS overall_rank
    FROM bridge.work_countries
    GROUP BY country_code
    ORDER BY count(*) DESC
    LIMIT 6
),
yearly AS (
    SELECT w.publication_year AS year, count(DISTINCT w.work_id) AS papers_with_country
    FROM core.works w
    JOIN bridge.work_countries wc USING (work_id)
    WHERE w.publication_year >= 2023
    GROUP BY w.publication_year
)
SELECT w.publication_year                                   AS year,
       t.overall_rank,
       wc.country_code,
       p.name                                               AS country,
       count(*)                                             AS papers,
       y.papers_with_country,
       round(100.0 * count(*) / y.papers_with_country, 1)   AS share_pct
FROM core.works w
JOIN bridge.work_countries wc USING (work_id)
JOIN top t USING (country_code)
JOIN yearly y ON y.year = w.publication_year
LEFT JOIN external.country_profiles p USING (country_code)
GROUP BY w.publication_year, t.overall_rank, wc.country_code, p.name, y.papers_with_country
ORDER BY year, t.overall_rank;
