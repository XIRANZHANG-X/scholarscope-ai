-- RAG papers per country, all years (2019-01-01 .. 2026-09-22), full counting:
-- a paper with authors from 3 countries counts once for each of them.
-- Same source as work_countries.csv (bridge.work_countries).
SELECT country_code,
       count(DISTINCT work_id) AS papers
FROM bridge.work_countries
GROUP BY country_code
ORDER BY papers DESC;
