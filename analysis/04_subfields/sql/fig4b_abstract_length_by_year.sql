-- Abstract and title length by year: a check on the mention shares.
-- A longer abstract has more room to mention things, so if abstracts grow, every share measured
-- on "title or abstract" drifts upwards for a reason that has nothing to do with research
-- interests. Titles grow much less, which is why the title-only columns serve as the control.
SELECT publication_year                              AS year,
       count(*)                                      AS papers_with_abstract,
       round(avg(length(abstract)))                  AS mean_abstract_chars,
       round(avg(length(title)))                     AS mean_title_chars
FROM core.works
WHERE abstract IS NOT NULL AND publication_year IN (2024, 2025, 2026)
GROUP BY publication_year
ORDER BY year;
