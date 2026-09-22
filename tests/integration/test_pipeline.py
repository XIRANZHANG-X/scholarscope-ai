from datetime import date

import pytest

from scholarscope.ingestion import pipeline, runs
from scholarscope.ingestion.openalex_client import OpenAlexError
from scholarscope.ingestion.raw_cache import RawCache
from tests.support import TWO_QUERY_RECALL, FakeOpenAlex

pytestmark = pytest.mark.db

TO_DATE = date(2026, 9, 22)
UNCAPPED = pipeline.Profile("test", None, 1.0, pipeline.BY_DATE)


@pytest.fixture
def api(works_page) -> FakeOpenAlex:
    w1, w2, w3 = works_page["results"]
    return FakeOpenAlex({"large language model": [[w1, w2], [w3]], "retrieval augmented generation": [[w3]]})


def run(db, api, tmp_path, profile=UNCAPPED, **client_kwargs) -> pipeline.IngestResult:
    return pipeline.ingest(
        db, api.client(**client_kwargs), RawCache(tmp_path), TWO_QUERY_RECALL, profile, to_date=TO_DATE
    )


def scalar(db, query: str, *params):
    return db.execute(query, params).fetchone()[0]


def test_ingest_loads_every_page_and_records_the_run(db, api, tmp_path):
    result = run(db, api, tmp_path)

    assert (result.status, result.works_fetched) == ("succeeded", 4)
    assert result.cost_usd == pytest.approx(0.003)
    assert scalar(db, "SELECT count(*) FROM core.works") == 3
    assert scalar(db, "SELECT count(*) FROM meta.work_recall_hits") == 4
    assert db.execute(
        "SELECT status, requests_made, records_fetched FROM meta.ingestion_runs WHERE run_id = %s", (result.run_id,)
    ).fetchone() == ("succeeded", 3, 4)
    assert scalar(db, "SELECT bool_and(is_exhausted) FROM meta.ingestion_checkpoints WHERE run_id = %s", result.run_id)
    assert len(list(tmp_path.rglob("page-*.json.gz"))) == 3
    assert "to_publication_date:2026-09-22" in api.requests[0]["filter"]
    assert {r["sort"] for r in api.requests} == {"publication_date:asc"}


def test_second_run_updates_instead_of_duplicating(db, api, tmp_path):
    first = run(db, api, tmp_path)
    second = run(db, api, tmp_path)
    assert scalar(db, "SELECT count(*) FROM core.works") == 3
    assert db.execute("SELECT DISTINCT first_run_id, last_run_id FROM core.works").fetchall() == [
        (first.run_id, second.run_id)
    ]


def test_failed_run_resumes_from_its_checkpoint(db, api, tmp_path):
    api.fail("large language model", "p1", 500)
    with pytest.raises(OpenAlexError):
        run(db, api, tmp_path, max_attempts=2)
    run_id = scalar(db, "SELECT max(run_id) FROM meta.ingestion_runs")
    assert runs.get_run(db, run_id).status == "failed"
    assert runs.get_checkpoint(db, run_id, "llm.large_language_model").pages_done == 1
    assert scalar(db, "SELECT count(*) FROM core.works") == 2

    api.heal()
    api.requests.clear()
    result = pipeline.resume(db, api.client(), RawCache(tmp_path), run_id)

    assert (result.run_id, result.status) == (run_id, "succeeded")
    assert [r["cursor"] for r in api.requests] == ["p1", "*"]
    assert scalar(db, "SELECT count(*) FROM core.works") == 3


def test_cost_limit_stops_as_partial_and_can_resume(db, api, tmp_path):
    result = run(db, api, tmp_path, profile=pipeline.Profile("test", None, 0.001, pipeline.BY_DATE))
    assert result.status == "partial"
    assert f"--resume {result.run_id}" in result.message
    assert (result.works_fetched, len(api.requests)) == (2, 1)


def test_exhausted_daily_budget_is_partial_not_failed(db, api, tmp_path):
    api.fail("large language model", "*", 429, {"Retry-After": "86400"})
    result = run(db, api, tmp_path)
    assert result.status == "partial"
    assert runs.get_run(db, result.run_id).status == "partial"


def test_profile_cap_limits_works_per_query(db, api, tmp_path):
    result = run(db, api, tmp_path, profile=pipeline.Profile("test", 1, 1.0, pipeline.BY_RELEVANCE))
    assert (result.status, result.works_fetched) == ("succeeded", 2)
    assert [r["per_page"] for r in api.requests] == ["1", "1"]
    assert {r["sort"] for r in api.requests} == {"relevance_score:desc"}
