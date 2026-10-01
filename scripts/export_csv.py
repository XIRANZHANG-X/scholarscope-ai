"""Export the corpus as CSV files, for teammates who work in Tableau, Excel, R or pandas.

    uv run python scripts/export_csv.py [OUT_DIR]

Writes to `data/export/` by default. Every file is UTF-8 with a header row. The wide files
(`works.csv`, `country_indicators_wide.csv`) are denormalised on purpose: one row per paper and
one row per country-year, which is the shape BI tools want. The narrow files preserve the
relationships for anyone who would rather join them back together.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scholarscope.config import Settings
from scholarscope.db import connect

# Each entry is (file name, SQL). Order only affects the progress output.
EXPORTS: list[tuple[str, str]] = [
    # One row per paper, with the counts and the primary topic already joined on.
    # `date_is_year_only` marks the 2,417 papers OpenAlex dates 1 January because it knows
    # only their year — filter on it before drawing anything monthly.
    ("works.csv", """
        SELECT w.work_id, w.doi, w.title, w.publication_date, w.publication_year,
               (to_char(w.publication_date, 'MM-DD') = '01-01') AS date_is_year_only,
               w.type, w.language, w.is_oa, w.oa_status,
               w.cited_by_count, w.fwci, w.citation_percentile, w.is_top_10pct, w.is_top_1pct,
               w.referenced_works_count, w.is_retracted,
               s.display_name AS source_name, s.type AS source_type,
               (SELECT count(*) FROM bridge.work_authors a WHERE a.work_id = w.work_id) AS n_authors,
               (SELECT count(*) FROM bridge.work_institutions i WHERE i.work_id = w.work_id) AS n_institutions,
               (SELECT count(*) FROM bridge.work_countries c WHERE c.work_id = w.work_id) AS n_countries,
               (SELECT string_agg(c.country_code, ';' ORDER BY c.country_code)
                  FROM bridge.work_countries c WHERE c.work_id = w.work_id) AS country_codes,
               t.display_name AS primary_topic, t.subfield_name AS primary_subfield,
               t.field_name AS primary_field
        FROM core.works w
        LEFT JOIN core.sources s ON s.source_id = w.source_id
        LEFT JOIN bridge.work_topics wt ON wt.work_id = w.work_id AND wt.is_primary
        LEFT JOIN core.topics t ON t.topic_id = wt.topic_id
        ORDER BY w.publication_date, w.work_id
    """),
    # Kept out of works.csv: abstracts carry newlines and quadruple the file size.
    ("work_abstracts.csv", """
        SELECT work_id, title, abstract FROM core.works WHERE abstract IS NOT NULL ORDER BY work_id
    """),
    ("work_authors.csv", """
        SELECT a.work_id, a.author_seq, a.author_id, a.raw_author_name, a.author_position,
               a.is_corresponding,
               array_to_string(a.raw_affiliation_strings, ' | ') AS raw_affiliations
        FROM bridge.work_authors a ORDER BY a.work_id, a.author_seq
    """),
    ("authors.csv", "SELECT author_id, display_name, orcid FROM core.authors ORDER BY author_id"),
    # Institutions with the ROR name and country the crosswalk recovered, where OpenAlex had none.
    ("institutions.csv", """
        SELECT i.institution_id, i.display_name, i.type,
               coalesce(i.ror_id, c.ror_id) AS ror_id,
               coalesce(i.country_code, o.country_code) AS country_code,
               o.display_name AS ror_name, o.country_name, o.city,
               o.latitude, o.longitude, c.match_method
        FROM core.institutions i
        LEFT JOIN external.institution_crosswalk c ON c.institution_id = i.institution_id
        LEFT JOIN external.ror_organizations o ON o.ror_id = coalesce(i.ror_id, c.ror_id)
        ORDER BY i.institution_id
    """),
    ("work_institutions.csv", "SELECT work_id, institution_id FROM bridge.work_institutions ORDER BY work_id"),
    ("work_countries.csv", "SELECT work_id, country_code FROM bridge.work_countries ORDER BY work_id"),
    ("work_topics.csv", """
        SELECT wt.work_id, wt.topic_id, wt.score, wt.is_primary,
               t.display_name AS topic, t.subfield_name, t.field_name, t.domain_name
        FROM bridge.work_topics wt JOIN core.topics t USING (topic_id)
        ORDER BY wt.work_id, wt.score DESC
    """),
    ("work_keywords.csv", """
        SELECT wk.work_id, wk.keyword_id, k.display_name AS keyword, wk.score
        FROM bridge.work_keywords wk JOIN core.keywords k USING (keyword_id)
        ORDER BY wk.work_id, wk.score DESC
    """),
    # 90.9% of these point outside the corpus; `is_internal` says which edges form the inner graph.
    ("work_references.csv", """
        SELECT r.work_id, r.referenced_work_id,
               EXISTS (SELECT 1 FROM core.works w WHERE w.work_id = r.referenced_work_id) AS is_internal
        FROM bridge.work_references r ORDER BY r.work_id
    """),
    ("work_yearly_citations.csv", """
        SELECT work_id, year, cited_by_count FROM core.work_yearly_citations ORDER BY work_id, year
    """),
    ("sources.csv", "SELECT source_id, display_name, type, issn_l, host_organization_name FROM core.sources ORDER BY source_id"),
    ("country_profiles.csv", """
        SELECT country_code, iso3_code, name, region, income_level, capital_city, latitude, longitude
        FROM external.country_profiles ORDER BY country_code
    """),
    ("country_indicators_long.csv", """
        SELECT ci.country_code, p.name AS country_name, ci.indicator_code, i.name AS indicator_name,
               ci.year, ci.value
        FROM external.country_indicators ci
        JOIN external.indicators i USING (indicator_code)
        LEFT JOIN external.country_profiles p USING (country_code)
        ORDER BY ci.country_code, ci.indicator_code, ci.year
    """),
    # One row per country-year: the shape Tableau and Excel want for a scatter or a map.
    ("country_indicators_wide.csv", """
        SELECT ci.country_code, p.iso3_code, p.name AS country_name, p.region, p.income_level,
               p.latitude, p.longitude, ci.year,
               max(ci.value) FILTER (WHERE ci.indicator_code = 'SP.POP.TOTL')       AS population,
               max(ci.value) FILTER (WHERE ci.indicator_code = 'NY.GDP.MKTP.CD')    AS gdp_usd,
               max(ci.value) FILTER (WHERE ci.indicator_code = 'NY.GDP.PCAP.CD')    AS gdp_per_capita_usd,
               max(ci.value) FILTER (WHERE ci.indicator_code = 'GB.XPD.RSDV.GD.ZS') AS rd_expenditure_pct_gdp,
               max(ci.value) FILTER (WHERE ci.indicator_code = 'SP.POP.SCIE.RD.P6') AS researchers_per_million,
               max(ci.value) FILTER (WHERE ci.indicator_code = 'TX.VAL.TECH.MF.ZS') AS high_tech_exports_pct
        FROM external.country_indicators ci
        LEFT JOIN external.country_profiles p USING (country_code)
        GROUP BY 1,2,3,4,5,6,7,8 ORDER BY 1, 8
    """),
    ("ror_organizations.csv", """
        SELECT ror_id, display_name, status, established,
               array_to_string(types, ';') AS types,
               array_to_string(acronyms, ';') AS acronyms,
               country_code, country_name, subdivision_name, city, latitude, longitude,
               continent_code, website, wikidata_id
        FROM external.ror_organizations ORDER BY ror_id
    """),
    # Parent/child/related pairs where both ends are institutions in the corpus.
    ("institution_relationships.csv", """
        SELECT r.institution_id, a.display_name AS institution_name,
               r.related_institution_id, b.display_name AS related_institution_name,
               r.relationship_type
        FROM bridge.institution_relationships r
        LEFT JOIN core.institutions a ON a.institution_id = r.institution_id
        LEFT JOIN core.institutions b ON b.institution_id = r.related_institution_id
        ORDER BY r.institution_id, r.related_institution_id
    """),
    # The macro context: how the four themes grew. Counts only - we downloaded RAG's papers alone.
    ("theme_year_counts.csv", """
        SELECT theme, field_scope, bucket AS year, sum(works_count) AS works
        FROM meta.recall_probes WHERE dimension = 'publication_year'
        GROUP BY 1,2,3 ORDER BY 1,2,3
    """),
    ("data_quality.csv", """
        SELECT check_name, failing_rows, total_rows, checked_at
        FROM meta.data_quality_checks ORDER BY checked_at DESC, check_name
    """),
]


def main(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    total = 0
    with connect(settings) as conn:
        for name, sql in EXPORTS:
            path = out_dir / name
            with path.open("wb") as handle, conn.cursor() as cur:
                with cur.copy(f"COPY ({sql}) TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
                    for chunk in copy:
                        handle.write(chunk)
            size = path.stat().st_size
            total += size
            print(f"{name:34} {size / 1_048_576:8.2f} MB")
    print(f"{'TOTAL':34} {total / 1_048_576:8.2f} MB  ->  {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/export")))
