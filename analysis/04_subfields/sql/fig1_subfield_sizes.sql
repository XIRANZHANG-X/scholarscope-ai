-- How many RAG papers mention each sub-direction.
--
-- :rules is the content of subfield_rules.csv, passed in as JSON by make_figures.py. Each rule
-- is a case-insensitive PostgreSQL regular expression; a paper gets a sub-direction when its
-- title or abstract matches. Tags are not exclusive: a paper can carry several, or none.
--
--   papers        the rule matches the title or the abstract  (broad: "mentions it")
--   papers_title  the rule matches the title itself           (strict: "is about it")
--
-- Only the 13,969 papers that have an abstract are used, so every paper had the same chance to
-- match; shares are out of those.
WITH rules AS (
    SELECT *
    FROM jsonb_to_recordset(CAST(:rules AS jsonb)) AS r(subfield text, label text, kind text, pattern text)
),
base AS (
    SELECT work_id, title, title || ' ' || abstract AS body
    FROM core.works
    WHERE abstract IS NOT NULL
)
SELECT r.subfield, r.label, r.kind,
       count(*) FILTER (WHERE b.body  ~* r.pattern)                               AS papers,
       count(*) FILTER (WHERE b.title ~* r.pattern)                               AS papers_title,
       count(*)                                                                   AS papers_with_abstract,
       round(100.0 * count(*) FILTER (WHERE b.body  ~* r.pattern) / count(*), 1)  AS share_pct,
       round(100.0 * count(*) FILTER (WHERE b.title ~* r.pattern) / count(*), 1)  AS share_title_pct
FROM rules r
CROSS JOIN base b
GROUP BY r.subfield, r.label, r.kind
ORDER BY papers DESC;
