from dataclasses import asdict
from pathlib import Path

import psycopg
import pytest

from scholarscope.cli import main
from scholarscope.external.worldbank import INDICATORS
from scholarscope.ingestion import runs
from scholarscope.ingestion.pipeline import PROFILES
from tests.support import (
    TWO_QUERY_RECALL,
    TWO_QUERY_RECALL_TOML,
    FakeOpenAlex,
    FakeRorApi,
    FakeWorldBankApi,
    world_bank_country,
)

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


ROR_DUMP = Path(__file__).parents[1] / "fixtures" / "ror" / "ror_sample.zip"


def test_ror_command_enriches_and_reports(db, works_page, cli_settings, capsys):
    from scholarscope.ingestion.loader import load_works
    from scholarscope.ingestion.transform import transform_work
    from scholarscope.ingestion import runs as ingestion_runs

    run_id = ingestion_runs.start_run(db, "test", {})
    with db.transaction():
        load_works(db, [transform_work(w) for w in works_page["results"]], run_id=run_id, query_key="rag")
    api = FakeRorApi({})

    exit_code = main(
        ["ror", "--dump", str(ROR_DUMP), "--no-match"], settings=cli_settings, http=api.http()
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "3 institutions linked" in out
    assert db.execute("SELECT count(*) FROM external.institution_crosswalk").fetchone() == (3,)


def test_worldbank_command_enriches_and_reports(db, cli_settings, capsys):
    api = FakeWorldBankApi(
        [world_bank_country("SG", "SGP", "Singapore")],
        {code: [("SG", 2024, 1.0)] for code in INDICATORS},
    )

    exit_code = main(["worldbank", "--from-year", "2019", "--to-year", "2026"], settings=cli_settings, http=api.http())

    assert exit_code == 0
    assert "1 countries" in capsys.readouterr().out
    assert db.execute("SELECT count(*) FROM external.country_profiles").fetchone() == (1,)


def test_worldbank_command_exits_3_on_api_error(db, cli_settings, capsys):
    api = FakeWorldBankApi([world_bank_country("SG", "SGP", "Singapore")], {})
    api.fail_indicator = "SP.POP.TOTL"

    exit_code = main(["worldbank"], settings=cli_settings, http=api.http())

    assert exit_code == 3
    assert "World Bank enrichment failed" in capsys.readouterr().err
    assert db.execute(
        "SELECT status FROM meta.ingestion_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone() == ("failed",)
