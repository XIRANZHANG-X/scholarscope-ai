from dataclasses import asdict

import psycopg
import pytest

from scholarscope.cli import main
from scholarscope.ingestion import runs
from scholarscope.ingestion.pipeline import PROFILES
from tests.support import TWO_QUERY_RECALL, TWO_QUERY_RECALL_TOML, FakeOpenAlex

pytestmark = pytest.mark.db


@pytest.fixture
def cli_settings(settings, test_db, tmp_path):
    return settings.model_copy(update={"postgres_db": test_db, "raw_data_dir": tmp_path / "raw"})


@pytest.fixture
def recall_file(tmp_path):
    path = tmp_path / "recall.toml"
    path.write_text(TWO_QUERY_RECALL_TOML, encoding="utf-8")
    return path


def test_ingest_then_quality(db, works_page, cli_settings, recall_file, capsys):
    w1, w2, w3 = works_page["results"]
    api = FakeOpenAlex({"large language model": [[w1, w2]], "retrieval augmented generation": [[w3]]})

    exit_code = main(["ingest", "--profile", "smoke", "--recall", str(recall_file)], settings=cli_settings, http=api.http())
    assert exit_code == 0
    assert "succeeded, 3 works" in capsys.readouterr().out
    assert [r["per_page"] for r in api.requests] == ["100", "100"]  # full pages: smoke allows 1 000 per query
    assert {r["sort"] for r in api.requests} == {"relevance_score:desc"}

    run_id = db.execute("SELECT max(run_id) FROM meta.ingestion_runs").fetchone()[0]
    assert main(["quality", "--run", str(run_id)], settings=cli_settings) == 0
    assert "works_without_recall_hit" in capsys.readouterr().out


def test_ingest_unexpected_error_exits_3_and_records_failed_run(db, cli_settings, recall_file, capsys):
    api = FakeOpenAlex({"large language model": [[]], "retrieval augmented generation": [[]]})
    api.fail("large language model", "*", 400)

    exit_code = main(["ingest", "--profile", "smoke", "--recall", str(recall_file)], settings=cli_settings, http=api.http())

    assert exit_code == 3
    run_id, status = db.execute(
        "SELECT run_id, status FROM meta.ingestion_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    assert status == "failed"
    err = capsys.readouterr().err
    assert str(run_id) in err
    assert "--resume" in err


def test_ingest_failure_before_run_row_exists_cannot_be_resumed(db, cli_settings, recall_file, capsys, monkeypatch):
    """A psycopg.Error raised before `runs.start_run`'s INSERT commits leaves no run row at all
    (e.g. the INSERT itself fails), so there is nothing this attempt could resume."""

    def _raise(*_args, **_kwargs):
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr("scholarscope.cli.pipeline.ingest", _raise)

    exit_code = main(
        ["ingest", "--profile", "smoke", "--recall", str(recall_file)],
        settings=cli_settings,
        http=FakeOpenAlex({}).http(),
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "--resume" not in err
    assert "never started" in err or "did not start" in err
    assert db.execute("SELECT count(*) FROM meta.ingestion_runs").fetchone() == (0,)


def test_ingest_resume_failure_names_the_run(db, cli_settings, capsys):
    """When --resume is given, the failed run is the one named on the command line, regardless
    of whether it's the newest row in meta.ingestion_runs."""
    params = {
        "profile": asdict(PROFILES["smoke"]),
        "to_date": "2026-09-23",
        "recall": TWO_QUERY_RECALL.to_dict(),
    }
    run_id = runs.start_run(db, "smoke", params)

    api = FakeOpenAlex({})
    api.fail("large language model", "*", 400)

    exit_code = main(["ingest", "--resume", str(run_id)], settings=cli_settings, http=api.http())

    assert exit_code == 3
    err = capsys.readouterr().err
    assert f"run {run_id}" in err
    assert f"--resume {run_id}" in err


def test_probe_prints_summary(db, cli_settings, recall_file, capsys):
    api = FakeOpenAlex({}, groups={"publication_year": [("2025", 40)], "type": [("article", 40)]})

    assert main(["probe", "--recall", str(recall_file)], settings=cli_settings, http=api.http()) == 0

    out = capsys.readouterr().out
    assert "llm.large_language_model" in out and "rag.retrieval_augmented_generation" in out
    assert db.execute("SELECT count(*) FROM meta.recall_probes").fetchone() == (8,)
