import pytest

from scholarscope.cli import main
from tests.support import TWO_QUERY_RECALL_TOML, FakeOpenAlex

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


def test_probe_prints_summary(db, cli_settings, recall_file, capsys):
    api = FakeOpenAlex({}, groups={"publication_year": [("2025", 40)], "type": [("article", 40)]})

    assert main(["probe", "--recall", str(recall_file)], settings=cli_settings, http=api.http()) == 0

    out = capsys.readouterr().out
    assert "llm.large_language_model" in out and "rag.retrieval_augmented_generation" in out
    assert db.execute("SELECT count(*) FROM meta.recall_probes").fetchone() == (8,)
