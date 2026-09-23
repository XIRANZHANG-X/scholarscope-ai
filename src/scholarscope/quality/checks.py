"""SQL data-quality checks over the loaded corpus, recorded in meta.data_quality_checks.

A check with a threshold is a gate (passed = failing/total <= threshold). A check without one is a
tracked metric for the evaluation contract (简历深扒 §7.1): coverage and missingness, reported as-is.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class QualityCheck:
    name: str
    description: str
    sql: str  # returns exactly one row: (failing_rows, total_rows)
    threshold: float | None


@dataclass(frozen=True)
class CheckResult:
    name: str
    failing_rows: int
    total_rows: int
    threshold: float | None
    passed: bool | None

    @property
    def ratio(self) -> float:
        return self.failing_rows / self.total_rows if self.total_rows else 0.0


CHECKS = (
    QualityCheck(
        "works_without_recall_hit",
        "Works with no recorded inclusion reason in meta.work_recall_hits",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM meta.work_recall_hits h WHERE h.work_id = w.work_id)), count(*) FROM core.works w",
        0.0,
    ),
    QualityCheck(
        "works_date_out_of_scope",
        "Works published before 2019-01-01 or after the current date",
        "SELECT count(*) FILTER (WHERE publication_date < DATE '2019-01-01' OR publication_date > CURRENT_DATE), "
        "count(*) FROM core.works",
        0.0,
    ),
    QualityCheck(
        "works_missing_title",
        "Works with a NULL or blank title",
        "SELECT count(*) FILTER (WHERE coalesce(btrim(title), '') = ''), count(*) FROM core.works",
        0.01,
    ),
    QualityCheck(
        "works_duplicate_doi",
        "Works whose DOI is shared with at least one other work",
        "SELECT coalesce(sum(n) FILTER (WHERE n > 1), 0), (SELECT count(*) FROM core.works) "
        "FROM (SELECT count(*) AS n FROM core.works WHERE doi IS NOT NULL GROUP BY doi) AS per_doi",
        0.01,
    ),
    QualityCheck(
        "works_missing_abstract",
        "Works without an abstract",
        "SELECT count(*) FILTER (WHERE abstract IS NULL), count(*) FROM core.works",
        None,
    ),
    QualityCheck(
        "authorships_missing_author_id",
        "Authorships whose author OpenAlex has not disambiguated",
        "SELECT count(*) FILTER (WHERE author_id IS NULL), count(*) FROM bridge.work_authors",
        None,
    ),
    QualityCheck(
        "works_without_institution",
        "Works with no institution on any authorship (common for preprints)",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM bridge.authorship_institutions ai WHERE ai.work_id = w.work_id)), count(*) "
        "FROM core.works w",
        None,
    ),
    QualityCheck(
        "institutions_missing_ror",
        "Institutions without a ROR ID (input to the ROR matching step)",
        "SELECT count(*) FILTER (WHERE ror_id IS NULL), count(*) FROM core.institutions",
        None,
    ),
    QualityCheck(
        "references_outside_corpus",
        "Reference edges whose cited work is not in core.works",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM core.works w WHERE w.work_id = r.referenced_work_id)), count(*) "
        "FROM bridge.work_references r",
        None,
    ),
    QualityCheck(
        "institutions_without_ror",
        "Institutions with no ROR id in the crosswalk (Plan 2 enrichment)",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM external.institution_crosswalk c WHERE c.institution_id = i.institution_id)), "
        "count(*) FROM core.institutions i",
        None,
    ),
    QualityCheck(
        "corpus_countries_without_profile",
        "Countries appearing in the corpus that the World Bank does not list (e.g. Taiwan)",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM external.country_profiles p WHERE p.country_code = c.country_code)), count(*) "
        "FROM (SELECT DISTINCT country_code FROM bridge.work_countries) c",
        None,
    ),
    QualityCheck(
        "works_without_country",
        "Works with no country attribution, after ROR affiliation recovery",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM bridge.work_countries wc WHERE wc.work_id = w.work_id)), count(*) FROM core.works w",
        None,
    ),
    QualityCheck(
        "rd_indicator_missing",
        "Country-year cells with no R&D spending value (the World Bank reporting lag)",
        "SELECT count(*) FILTER (WHERE value IS NULL), count(*) FROM external.country_indicators "
        "WHERE indicator_code = 'GB.XPD.RSDV.GD.ZS'",
        None,
    ),
)

INSERT_RESULT = (
    "INSERT INTO meta.data_quality_checks "
    "(run_id, check_name, description, failing_rows, total_rows, threshold, passed) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)


def run_quality_checks(conn: psycopg.Connection, run_id: int | None = None) -> list[CheckResult]:
    results = []
    with conn.transaction():
        for check in CHECKS:
            failing, total = (int(v) for v in conn.execute(check.sql).fetchone())
            passed = None if check.threshold is None else (total == 0 or failing / total <= check.threshold)
            conn.execute(INSERT_RESULT, (run_id, check.name, check.description, failing, total, check.threshold, passed))
            results.append(CheckResult(check.name, failing, total, check.threshold, passed))
    return results
