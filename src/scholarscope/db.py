"""PostgreSQL connections and schema migrations."""

from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL

from scholarscope.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONNECT_TIMEOUT_S = 5


def connect(settings: Settings, dbname: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    """Open a psycopg connection to `dbname` (default: the configured project database)."""
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=dbname or settings.postgres_db,
        autocommit=autocommit,
        connect_timeout=CONNECT_TIMEOUT_S,
    )


def sqlalchemy_url(settings: Settings, dbname: str | None = None) -> URL:
    """SQLAlchemy URL for the same database; URL.create escapes special characters in the password."""
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=dbname or settings.postgres_db,
        query={"connect_timeout": str(CONNECT_TIMEOUT_S)},
    )


def alembic_config(url: URL) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "sql" / "migrations"))
    cfg.attributes["sqlalchemy_url"] = url
    return cfg


def migrate(settings: Settings, dbname: str | None = None, revision: str = "head") -> None:
    """Upgrade `dbname` to `revision`."""
    command.upgrade(alembic_config(sqlalchemy_url(settings, dbname)), revision)


def downgrade(settings: Settings, dbname: str | None = None, revision: str = "base") -> None:
    """Downgrade `dbname` to `revision`."""
    command.downgrade(alembic_config(sqlalchemy_url(settings, dbname)), revision)
