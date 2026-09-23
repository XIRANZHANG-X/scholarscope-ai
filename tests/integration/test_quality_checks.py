import pytest

from scholarscope.ingestion import runs
from scholarscope.ingestion.loader import load_works
from scholarscope.ingestion.transform import transform_work
from scholarscope.quality.checks import CHECKS, run_quality_checks

pytestmark = pytest.mark.db


def load_fixture(db, works: list[dict]) -> int:
    run_id = runs.start_run(db, "test", {})
    with db.transaction():
        load_works(db, [transform_work(w) for w in works], run_id=run_id, query_key="llm.large_language_model")
    return run_id


def test_checks_on_the_fixture_corpus(db, works_page):
    run_id = load_fixture(db, works_page["results"])

    results = {r.name: r for r in run_quality_checks(db, run_id)}

    assert results["works_without_recall_hit"].passed is True
    assert results["works_date_out_of_scope"].passed is True
    missing_author = results["authorships_missing_author_id"]
    assert (missing_author.failing_rows, missing_author.total_rows, missing_author.passed) == (1, 6, None)
    no_institution = results["works_without_institution"]
    assert (no_institution.failing_rows, no_institution.total_rows) == (1, 3)
    assert results["references_outside_corpus"].failing_rows == 5
    assert db.execute(
        "SELECT count(*) FROM meta.data_quality_checks WHERE run_id = %s", (run_id,)
    ).fetchone() == (len(CHECKS),)


def test_work_without_inclusion_reason_fails_the_gate(db, works_page):
    run_id = load_fixture(db, works_page["results"])
    db.execute("DELETE FROM meta.work_recall_hits WHERE work_id = 'W4389984066'")

    result = {r.name: r for r in run_quality_checks(db, run_id)}["works_without_recall_hit"]

    assert (result.failing_rows, result.passed) == (1, False)


def test_shared_doi_counts_every_work_involved(db, works_page):
    works_page["results"][1]["doi"] = works_page["results"][0]["doi"]
    run_id = load_fixture(db, works_page["results"])

    result = {r.name: r for r in run_quality_checks(db, run_id)}["works_duplicate_doi"]

    assert (result.failing_rows, result.total_rows, result.passed) == (2, 3, False)
