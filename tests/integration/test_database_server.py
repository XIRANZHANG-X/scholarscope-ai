import pytest

pytestmark = pytest.mark.db


def test_server_matches_compose_configuration(db):
    """Guards compose.yaml: PostgreSQL 17, pgvector 0.8.6 available, memory settings from architecture §12.3."""
    assert db.execute("SHOW server_version_num").fetchone()[0].startswith("17")
    assert db.execute("SELECT default_version FROM pg_available_extensions WHERE name = 'vector'").fetchone() == (
        "0.8.6",
    )
    assert db.execute("SHOW shared_buffers").fetchone() == ("2GB",)
    assert db.execute("SHOW maintenance_work_mem").fetchone() == ("1GB",)
