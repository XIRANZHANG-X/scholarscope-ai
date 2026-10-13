-- How many papers of each publication type have a country at all.
-- Every country figure rests on the papers counted in `with_country`; a type that is mostly
-- missing here is mostly missing from the country rankings too.
-- `with_affiliation_text` shows the cause: no country can be recovered for a paper whose
-- authorships carry no affiliation string.
SELECT w.type,
       count(*)                                                                    AS works,
       count(*) FILTER (WHERE c.work_id IS NOT NULL)                               AS with_country,
       round(100.0 * count(*) FILTER (WHERE c.work_id IS NOT NULL) / count(*), 1)  AS with_country_pct,
       count(*) FILTER (WHERE a.work_id IS NOT NULL)                               AS with_affiliation_text,
       round(100.0 * count(*) FILTER (WHERE a.work_id IS NOT NULL) / count(*), 1)  AS with_affiliation_text_pct
FROM core.works w
LEFT JOIN (SELECT DISTINCT work_id FROM bridge.work_countries) c USING (work_id)
LEFT JOIN (SELECT DISTINCT work_id FROM bridge.work_authors
           WHERE cardinality(raw_affiliation_strings) > 0) a USING (work_id)
GROUP BY w.type
ORDER BY works DESC;
