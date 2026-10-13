-- How many papers of each publication year have a country at all (2023 on; earlier years have
-- under 20 papers each). See fig6a for the same split by publication type.
SELECT w.publication_year                                                          AS year,
       count(*)                                                                    AS works,
       count(*) FILTER (WHERE c.work_id IS NOT NULL)                               AS with_country,
       round(100.0 * count(*) FILTER (WHERE c.work_id IS NOT NULL) / count(*), 1)  AS with_country_pct
FROM core.works w
LEFT JOIN (SELECT DISTINCT work_id FROM bridge.work_countries) c USING (work_id)
WHERE w.publication_year >= 2023
GROUP BY w.publication_year
ORDER BY year;
