import json
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from scholarscope.config import Settings
from scholarscope.db import connect, migrate

FIXTURES = Path(__file__).parent / "fixtures"
TEST_DB = "scholarscope_test"
# Reference data that migrations seed; every other table is emptied after each test.
KEEP_TABLES = {"meta.schema_versions", "meta.data_sources"}


@pytest.fixture
def works_page() -> dict:
    """Three real OpenAlex works (CC0), trimmed; captured 2026-09-22. Fresh copy per test."""
    return json.loads((FIXTURES / "openalex" / "works_page.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def test_db(settings: Settings) -> str:
    """A freshly migrated database used by all `db` tests in the session."""
    try:
        admin = connect(settings, "postgres", autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.fail(
            f"PostgreSQL is not reachable ({exc}). Start it with `docker compose up -d`, "
            'or run only unit tests with `uv run pytest -m "not db"`.',
            pytrace=False,
        )
    with admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    migrate(settings, TEST_DB)
    return TEST_DB


@pytest.fixture
def db(settings: Settings, test_db: str):
    """Autocommit connection to the test database; all data is truncated afterwards."""
    with connect(settings, test_db, autocommit=True) as conn:
        yield conn
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT schemaname || '.' || tablename FROM pg_tables "
                "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
            ).fetchall()
            if name not in KEEP_TABLES
        ]
        conn.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
