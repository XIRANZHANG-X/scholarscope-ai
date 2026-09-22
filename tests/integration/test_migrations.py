import pytest
from psycopg import sql

from scholarscope.db import connect, downgrade, migrate

pytestmark = pytest.mark.db

MIGRATION_DB = "scholarscope_migration_test"
EXPECTED_RELATIONS = {
    "meta.schema_versions", "meta.data_sources", "meta.ingestion_runs", "meta.ingestion_checkpoints",
    "meta.data_quality_checks", "meta.recall_probes",
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
            "WHERE table_schema IN ('meta', 'core', 'bridge')"
        ).fetchall()
    return {r[0] for r in rows}


def test_upgrade_creates_every_table_and_view(settings, scratch_db):
    migrate(settings, scratch_db)
    assert relations(settings, scratch_db) == EXPECTED_RELATIONS
    with connect(settings, scratch_db) as conn:
        assert conn.execute("SELECT version_num FROM meta.schema_versions").fetchone() == ("0001",)
        assert conn.execute("SELECT license FROM meta.data_sources WHERE source_id = 'openalex'").fetchone() == (
            "CC0 1.0",
        )


def test_downgrade_to_base_then_upgrade_again(settings, scratch_db):
    migrate(settings, scratch_db)
    downgrade(settings, scratch_db, "base")
    assert relations(settings, scratch_db) == {"meta.schema_versions"}
    migrate(settings, scratch_db)
    assert relations(settings, scratch_db) == EXPECTED_RELATIONS
