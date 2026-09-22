import pytest

from scholarscope.ingestion import runs
from scholarscope.ingestion.loader import load_works
from scholarscope.ingestion.transform import transform_work

pytestmark = pytest.mark.db

COUNTED = (
    "core.works", "core.authors", "core.institutions", "core.sources", "core.topics", "core.keywords",
    "core.countries", "core.work_yearly_citations", "bridge.work_authors", "bridge.authorship_institutions",
    "bridge.authorship_countries", "bridge.work_topics", "bridge.work_keywords", "bridge.work_references",
    "meta.work_recall_hits",
)


def counts(db) -> dict[str, int]:
    return {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in COUNTED}


def load(db, works: list[dict], run_id: int, query_key: str = "llm.large_language_model") -> int:
    with db.transaction():
        return load_works(db, [transform_work(w) for w in works], run_id=run_id, query_key=query_key)


def test_loads_fixture_page_into_every_table(db, works_page):
    run_id = runs.start_run(db, "test", {})
    assert load(db, works_page["results"], run_id) == 3
    assert counts(db) == {
        "core.works": 3, "core.authors": 5, "core.institutions": 3, "core.sources": 3, "core.topics": 4,
        "core.keywords": 5, "core.countries": 2, "core.work_yearly_citations": 9, "bridge.work_authors": 6,
        "bridge.authorship_institutions": 4, "bridge.authorship_countries": 4, "bridge.work_topics": 6,
        "bridge.work_keywords": 6, "bridge.work_references": 5, "meta.work_recall_hits": 3,
    }


def test_reloading_is_idempotent(db, works_page):
    first = runs.start_run(db, "test", {})
    second = runs.start_run(db, "test", {})
    load(db, works_page["results"], first)
    before = counts(db)

    load(db, works_page["results"], second)

    assert counts(db) == before
    assert db.execute("SELECT DISTINCT first_run_id, last_run_id FROM core.works").fetchall() == [(first, second)]


def test_reload_reflects_a_changed_record(db, works_page):
    run_id = runs.start_run(db, "test", {})
    load(db, works_page["results"], run_id)
    changed = works_page["results"][0]
    changed["cited_by_count"] = 9999
    changed["authorships"] = changed["authorships"][:1]
    changed["referenced_works"] = []

    load(db, [changed], run_id)

    def one(query: str):
        return db.execute(query).fetchone()[0]

    assert one("SELECT cited_by_count FROM core.works WHERE work_id = 'W4384071683'") == 9999
    assert one("SELECT count(*) FROM bridge.work_authors WHERE work_id = 'W4384071683'") == 1
    assert one("SELECT count(*) FROM bridge.authorship_institutions WHERE work_id = 'W4384071683'") == 1
    assert one("SELECT count(*) FROM bridge.work_references WHERE work_id = 'W4384071683'") == 0


def test_work_matched_by_two_queries_keeps_both_reasons(db, works_page):
    run_id = runs.start_run(db, "test", {})
    load(db, works_page["results"], run_id, "llm.large_language_model")
    load(db, works_page["results"], run_id, "rag.retrieval_augmented_generation")
    assert db.execute("SELECT count(*) FROM core.works").fetchone() == (3,)
    assert db.execute("SELECT count(*) FROM meta.work_recall_hits").fetchone() == (6,)


def test_views_derive_institutions_and_affiliations(db, works_page):
    load(db, works_page["results"], runs.start_run(db, "test", {}))
    assert db.execute(
        "SELECT count(*) FROM bridge.work_institutions WHERE work_id = 'W4384071683'"
    ).fetchone() == (1,)
    assert db.execute(
        "SELECT first_year, last_year, works_count FROM bridge.author_affiliations WHERE author_id = 'A5027454515'"
    ).fetchone() == (2023, 2023, 1)
