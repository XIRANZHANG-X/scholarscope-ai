-- Add Taiwan to the World Bank tables. Run ONCE against the project database:
--
--   docker compose exec -T postgres psql -U scholarscope -d scholarscope < country_indicators_analysis/sql/00_add_taiwan.sql
--
-- Why: the World Bank has no record for Taiwan, so the 156 Taiwanese RAG papers have no country
-- indicators and silently drop out of any country-level analysis. The values below come from
-- Taiwan's own official statistics. Re-running is safe: nothing is inserted if TW already exists.
--
-- Every row in external.* carries a run_id (NOT NULL, references meta.ingestion_runs), so this
-- script registers the manual import as its own data source and ingestion run, the same way the
-- World Bank loader records provenance.
--
-- Sources
--   NY.GDP.PCAP.CD     2025  39,492 US$    DGBAS 2025-Q4 national accounts release, 2026-02-13 (preliminary)
--                                          https://www.dgbas.gov.tw/News_Content.aspx?n=3602&s=235719
--   GB.XPD.RSDV.GD.ZS  2024  4.10 %       NSTC National Science and Technology Survey, released 2025-10-31.
--                                          Secondary citation: the NSTC table itself could not be retrieved;
--                                          figure as reported by China Times:
--                                          https://wantrich.chinatimes.com/news/20251031900342-420501
--   SP.POP.TOTL        2025  23,299,132    Ministry of the Interior household registration, end of Dec 2025
--                                          (male 11,462,401 + female 11,836,731), https://www.ris.gov.tw

BEGIN;

INSERT INTO meta.data_sources (source_id, name, url, license, notes) VALUES
    ('taiwan_official', 'Taiwan official statistics (DGBAS, NSTC, MOI)', 'https://www.stat.gov.tw',
     'Open Government Data License, version 1.0',
     'Manual import: Taiwan is absent from World Bank Open Data. Three indicators, see sql/00_add_taiwan.sql.')
ON CONFLICT (source_id) DO NOTHING;

-- TW should already exist here (authorship_countries references it); kept for safety.
INSERT INTO core.countries (country_code, name) VALUES ('TW', 'Taiwan')
ON CONFLICT (country_code) DO NOTHING;

WITH run AS (
    INSERT INTO meta.ingestion_runs (source_id, profile, params, status, finished_at, records_fetched, message)
    SELECT 'taiwan_official', 'manual', '{"file": "country_indicators_analysis/sql/00_add_taiwan.sql"}'::jsonb,
           'succeeded', now(), 3, 'Taiwan profile and 3 indicators entered by hand from official statistics'
    WHERE NOT EXISTS (SELECT 1 FROM external.country_profiles WHERE country_code = 'TW')
    RETURNING run_id
), profile AS (
    INSERT INTO external.country_profiles
        (country_code, iso3_code, name, region, income_level, capital_city, latitude, longitude, run_id)
    SELECT 'TW', 'TWN', 'Taiwan', 'East Asia & Pacific', 'High income', 'Taipei', 25.0375, 121.5637, run_id
    FROM run
)
INSERT INTO external.country_indicators (country_code, indicator_code, year, value, run_id)
SELECT v.country_code, v.indicator_code, v.year, v.value, run.run_id
FROM run
CROSS JOIN (VALUES
    ('TW', 'NY.GDP.PCAP.CD',    2025::smallint, 39492::double precision),
    ('TW', 'GB.XPD.RSDV.GD.ZS', 2024::smallint, 4.10::double precision),
    ('TW', 'SP.POP.TOTL',       2025::smallint, 23299132::double precision)
) AS v(country_code, indicator_code, year, value);

COMMIT;
