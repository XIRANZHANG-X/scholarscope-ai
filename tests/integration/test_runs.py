from decimal import Decimal

import pytest

from scholarscope.ingestion import runs

pytestmark = pytest.mark.db


def test_run_lifecycle(db):
    run_id = runs.start_run(db, "smoke", {"to_date": "2026-09-22"})
    info = runs.get_run(db, run_id)
    assert (info.profile, info.status, info.params) == ("smoke", "running", {"to_date": "2026-09-22"})

    runs.finish_run(db, run_id, "partial", "cost limit reached")
    assert runs.get_run(db, run_id).status == "partial"

    runs.mark_running(db, run_id)
    row = db.execute(
        "SELECT status, finished_at, message FROM meta.ingestion_runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert row == ("running", None, None)


def test_unknown_run_raises(db):
    with pytest.raises(LookupError):
        runs.get_run(db, 999_999)


def test_checkpoints_default_then_track_pages(db):
    run_id = runs.start_run(db, "smoke", {})
    assert runs.get_checkpoint(db, run_id, "q") == runs.Checkpoint("q")

    runs.record_page(db, run_id, runs.Checkpoint("q", "abc", 1, 100, False), cost_usd=0.001, records=100)
    runs.record_page(db, run_id, runs.Checkpoint("q", None, 2, 150, True), cost_usd=0.001, records=50)

    assert runs.get_checkpoint(db, run_id, "q") == runs.Checkpoint("q", None, 2, 150, True)
    counters = db.execute(
        "SELECT requests_made, records_fetched, cost_usd FROM meta.ingestion_runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert counters == (2, 150, Decimal("0.0020"))
