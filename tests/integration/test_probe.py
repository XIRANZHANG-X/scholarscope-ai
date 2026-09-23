from datetime import UTC, date, datetime

import pytest

from scholarscope.ingestion import probe
from tests.support import TWO_QUERY_RECALL, FakeOpenAlex

pytestmark = pytest.mark.db


def test_probe_stores_bucket_counts_and_summarises_them_in_sql(db):
    api = FakeOpenAlex(
        {}, groups={"publication_year": [("2024", 10), ("2025", 20)], "type": [("article", 25), ("dataset", 5)]}
    )
    probed_at = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    cost = probe.run_probe(db, api.client(), TWO_QUERY_RECALL, to_date=date(2026, 9, 22), probed_at=probed_at)

    assert cost == pytest.approx(8 * 0.0001)  # 2 queries x 2 field scopes x 2 dimensions
    assert probe.probe_summary(db, probed_at) == [
        ("llm.large_language_model", "llm", 30, 15),
        ("rag.retrieval_augmented_generation", "rag", 30, 15),
    ]
    assert db.execute("SELECT count(*) FROM meta.recall_probes").fetchone() == (16,)
    year_filters = [r["filter"] for r in api.requests if r["group_by"] == "publication_year"]
    type_filters = [r["filter"] for r in api.requests if r["group_by"] == "type"]
    assert all("type:article" in f for f in year_filters)
    assert all("type:" not in f for f in type_filters)
