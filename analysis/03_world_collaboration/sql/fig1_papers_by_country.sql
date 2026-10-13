-- RAG papers per country, all years, full counting: a paper with authors in three countries
-- counts once for each of them.
--
-- bridge.work_countries is the best-effort country of each paper (OpenAlex authorship countries
-- plus affiliations recovered through ROR). Only 9,335 of the 15,921 papers have any country, so
-- `share_of_attributed_pct` is a share of those 9,335, not of the corpus.
-- Names, ISO-3 codes and capital coordinates come from the World Bank profiles; Taiwan's row was
-- added by country_indicators_analysis/sql/00_add_taiwan.sql.
WITH attributed AS (
    SELECT count(DISTINCT work_id) AS papers FROM bridge.work_countries
)
SELECT rank() OVER (ORDER BY count(*) DESC)               AS rank,
       wc.country_code,
       p.iso3_code,
       coalesce(p.name, c.name, wc.country_code)          AS country,
       p.region,
       p.income_level,
       count(*)                                           AS papers,
       round(100.0 * count(*) / a.papers, 2)              AS share_of_attributed_pct,
       p.latitude,
       p.longitude
FROM bridge.work_countries wc
CROSS JOIN attributed a
LEFT JOIN external.country_profiles p USING (country_code)
LEFT JOIN core.countries c USING (country_code)
GROUP BY wc.country_code, p.iso3_code, p.name, c.name, p.region, p.income_level, a.papers, p.latitude, p.longitude
ORDER BY papers DESC, country;
