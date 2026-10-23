-- Headline numbers for the website: the size and time span of the RAG corpus.
SELECT count(*)                                         AS works,
       min(publication_year)                            AS first_year,
       max(publication_year)                            AS last_year,
       count(*) FILTER (WHERE abstract IS NOT NULL)     AS works_with_abstract
FROM core.works;
