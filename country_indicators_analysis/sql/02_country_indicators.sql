-- World Bank indicators used in the model, one row per country-indicator-year.
-- Same source as country_indicators_long.csv; the script keeps each country's most recent non-null year.
--   NY.GDP.PCAP.CD     GDP per capita (current US$)
--   GB.XPD.RSDV.GD.ZS  R&D expenditure (% of GDP)
--   SP.POP.TOTL        Population, total
SELECT ci.country_code, ci.indicator_code, ci.year, ci.value
FROM external.country_indicators ci
WHERE ci.indicator_code IN ('NY.GDP.PCAP.CD', 'GB.XPD.RSDV.GD.ZS', 'SP.POP.TOTL')
  AND ci.value IS NOT NULL
ORDER BY ci.country_code, ci.indicator_code, ci.year;
