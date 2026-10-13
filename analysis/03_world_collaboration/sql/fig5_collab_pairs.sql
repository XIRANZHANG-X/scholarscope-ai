-- The 30 country pairs with the most co-authored RAG papers, with the capital coordinates the
-- map draws its links between (World Bank country profiles).
SELECT row_number() OVER (ORDER BY count(*) DESC, a.country_code, b.country_code) AS rank,
       a.country_code   AS country_code_a,
       pa.name          AS country_a,
       b.country_code   AS country_code_b,
       pb.name          AS country_b,
       count(*)         AS joint_papers,
       pa.latitude      AS latitude_a,
       pa.longitude     AS longitude_a,
       pb.latitude      AS latitude_b,
       pb.longitude     AS longitude_b
FROM bridge.work_countries a
JOIN bridge.work_countries b ON b.work_id = a.work_id AND a.country_code < b.country_code
JOIN external.country_profiles pa ON pa.country_code = a.country_code
JOIN external.country_profiles pb ON pb.country_code = b.country_code
GROUP BY a.country_code, pa.name, b.country_code, pb.name, pa.latitude, pa.longitude, pb.latitude, pb.longitude
ORDER BY joint_papers DESC, a.country_code, b.country_code
LIMIT 30;
