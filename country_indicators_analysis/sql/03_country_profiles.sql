-- Country names, World Bank region and income group. Same source as country_profiles.csv.
SELECT country_code, iso3_code, name, region, income_level
FROM external.country_profiles
ORDER BY country_code;
