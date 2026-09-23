"""Alembic environment: online migrations only, version table kept in meta.schema_versions."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

url = config.attributes.get("sqlalchemy_url")
if url is None:
    from scholarscope.config import Settings
    from scholarscope.db import sqlalchemy_url

    url = sqlalchemy_url(Settings())

engine = create_engine(url, poolclass=pool.NullPool)
with engine.connect() as connection:
    # The version table lives in `meta`, so the schema must exist before Alembic looks for it.
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS meta"))
    connection.commit()
    context.configure(
        connection=connection,
        version_table="schema_versions",
        version_table_schema="meta",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()
