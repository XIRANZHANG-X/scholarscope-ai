import psycopg
import pytest
from psycopg import sql

from scholarscope.db import connect, downgrade, migrate

pytestmark = pytest.mark.db

MIGRATION_DB = "scholarscope_migration_test"
EXPECTED_RELATIONS = {
    "meta.schema_versions", "meta.data_sources", "meta.ingestion_runs", "meta.ingestion_checkpoints",
    "meta.data_quality_checks", "meta.recall_probes", "meta.work_recall_hits",
    "core.countries", "core.sources", "core.topics", "core.keywords", "core.institutions", "core.authors",
    "core.works", "core.work_yearly_citations",
    "bridge.work_authors", "bridge.authorship_institutions", "bridge.authorship_countries", "bridge.work_topics",
    "bridge.work_keywords", "bridge.work_references", "bridge.work_institutions", "bridge.author_affiliations",
    "bridge.institution_relationships", "bridge.authorship_ror_institutions", "bridge.work_countries",
    "external.ror_organizations", "external.ror_relationships", "external.institution_crosswalk",
    "external.affiliation_matches", "external.country_profiles", "external.indicators",
    "external.country_indicators",
}


@pytest.fixture
def scratch_db(settings):
    drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(MIGRATION_DB))
    with connect(settings, "postgres", autocommit=True) as admin:
        admin.execute(drop)
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(MIGRATION_DB)))
    yield MIGRATION_DB
    with connect(settings, "postgres", autocommit=True) as admin:
        admin.execute(drop)


def relations(settings, dbname) -> set[str]:
    with connect(settings, dbname) as conn:
        rows = conn.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables "
            "WHERE table_schema IN ('meta', 'core', 'bridge', 'external')"
        ).fetchall()
    return {r[0] for r in rows}


def test_upgrade_creates_every_table_and_view(settings, scratch_db):
    migrate(settings, scratch_db)
    assert relations(settings, scratch_db) == EXPECTED_RELATIONS
    with connect(settings, scratch_db) as conn:
        assert conn.execute("SELECT version_num FROM meta.schema_versions").fetchone() == ("0003",)
        assert conn.execute("SELECT license FROM meta.data_sources WHERE source_id = 'openalex'").fetchone() == (
            "CC0 1.0",
        )


def test_downgrade_to_base_then_upgrade_again(settings, scratch_db):
    migrate(settings, scratch_db)
    downgrade(settings, scratch_db, "base")
    assert relations(settings, scratch_db) == {"meta.schema_versions"}
    migrate(settings, scratch_db)
    assert relations(settings, scratch_db) == EXPECTED_RELATIONS


def test_downgrade_keeps_data_sources_that_runs_reference(settings, scratch_db):
    migrate(settings, scratch_db)
    with connect(settings, scratch_db) as conn:
        conn.execute(
            "INSERT INTO meta.ingestion_runs (source_id, profile, params, status) "
            "VALUES ('ror', 'test', '{}'::jsonb, 'succeeded')"
        )
        conn.commit()

    downgrade(settings, scratch_db, "0002")

    with connect(settings, scratch_db) as conn:
        assert conn.execute(
            "SELECT source_id FROM meta.data_sources WHERE source_id = 'ror'"
        ).fetchone() == ("ror",)
        # Undo the synthetic run and the catalogue row it kept alive, so the head migration's
        # unconditional seed INSERT (unchanged by this fix) doesn't collide on re-upgrade below.
        conn.execute("DELETE FROM meta.ingestion_runs WHERE source_id = 'ror'")
        conn.execute("DELETE FROM meta.data_sources WHERE source_id = 'ror'")
        conn.commit()

    migrate(settings, scratch_db)


def test_work_id_format_is_enforced(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO core.works (work_id, publication_date, publication_year, type, first_run_id, last_run_id) "
            "VALUES ('not-an-id', '2024-01-01', 2024, 'article', 1, 1)"
        )
