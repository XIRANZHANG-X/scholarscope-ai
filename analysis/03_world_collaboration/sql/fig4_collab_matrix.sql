-- Co-authored papers between each pair of the twelve largest producers.
-- One row per ordered pair (A,B and B,A both appear), so the result pivots straight into a
-- square matrix. `papers_a` is country A's own total, for reading a pair against its size.
WITH top AS (
    SELECT country_code,
           count(*)                                AS papers,
           row_number() OVER (ORDER BY count(*) DESC) AS overall_rank
    FROM bridge.work_countries
    GROUP BY country_code
    ORDER BY count(*) DESC
    LIMIT 12
)
SELECT ta.overall_rank      AS rank_a,
       a.country_code       AS country_code_a,
       pa.name              AS country_a,
       ta.papers            AS papers_a,
       tb.overall_rank      AS rank_b,
       b.country_code       AS country_code_b,
       pb.name              AS country_b,
       count(*)             AS joint_papers,
       round(100.0 * count(*) / ta.papers, 1) AS share_of_a_pct
FROM bridge.work_countries a
JOIN bridge.work_countries b ON b.work_id = a.work_id AND b.country_code <> a.country_code
JOIN top ta ON ta.country_code = a.country_code
JOIN top tb ON tb.country_code = b.country_code
LEFT JOIN external.country_profiles pa ON pa.country_code = a.country_code
LEFT JOIN external.country_profiles pb ON pb.country_code = b.country_code
GROUP BY ta.overall_rank, a.country_code, pa.name, ta.papers, tb.overall_rank, b.country_code, pb.name
ORDER BY rank_a, rank_b;
