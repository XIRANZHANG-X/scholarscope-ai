"""meta schema: data sources, ingestion runs and checkpoints, quality checks, recall probes

Revision ID: 0001
Revises:
Create Date: 2026-09-22
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = [
    # The `meta` schema itself is created by env.py because Alembic's version table lives in it.
    """
    CREATE TABLE meta.data_sources (
        source_id     text PRIMARY KEY,
        name          text NOT NULL,
        url           text NOT NULL,
        license       text NOT NULL,
        notes         text,
        registered_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    INSERT INTO meta.data_sources (source_id, name, url, license, notes) VALUES
        ('openalex', 'OpenAlex', 'https://openalex.org', 'CC0 1.0',
         'Works, authors, institutions, sources, topics, keywords and references via the REST API.')
    """,
    """
    CREATE TABLE meta.ingestion_runs (
        run_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source_id       text NOT NULL REFERENCES meta.data_sources,
        profile         text NOT NULL,
        params          jsonb NOT NULL,
        status          text NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
        started_at      timestamptz NOT NULL DEFAULT now(),
        finished_at     timestamptz,
        requests_made   integer NOT NULL DEFAULT 0,
        records_fetched integer NOT NULL DEFAULT 0,
        cost_usd        numeric(10, 4) NOT NULL DEFAULT 0,
        message         text
    )
    """,
    """
    CREATE TABLE meta.ingestion_checkpoints (
        run_id        bigint NOT NULL REFERENCES meta.ingestion_runs ON DELETE CASCADE,
        query_key     text NOT NULL,
        next_cursor   text,
        pages_done    integer NOT NULL DEFAULT 0,
        works_fetched integer NOT NULL DEFAULT 0,
        is_exhausted  boolean NOT NULL DEFAULT false,
        updated_at    timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, query_key)
    )
    """,
    """
    CREATE TABLE meta.data_quality_checks (
        check_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id       bigint REFERENCES meta.ingestion_runs ON DELETE CASCADE,
        check_name   text NOT NULL,
        description  text NOT NULL,
        failing_rows bigint NOT NULL,
        total_rows   bigint NOT NULL,
        threshold    numeric,
        passed       boolean,
        checked_at   timestamptz NOT NULL DEFAULT now()
    )
    """,
    "COMMENT ON COLUMN meta.data_quality_checks.threshold IS "
    "'Maximum allowed failing_rows/total_rows; NULL means the check is a tracked metric, not a gate'",
    """
    CREATE TABLE meta.recall_probes (
        probe_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        probed_at   timestamptz NOT NULL,
        query_key   text NOT NULL,
        theme       text NOT NULL,
        field_scope text NOT NULL CHECK (field_scope IN ('all', 'cs')),
        dimension   text NOT NULL CHECK (dimension IN ('publication_year', 'type')),
        bucket      text NOT NULL,
        works_count integer NOT NULL,
        filter      text NOT NULL
    )
    """,
]

DOWNGRADE = [
    "DROP TABLE meta.recall_probes",
    "DROP TABLE meta.data_quality_checks",
    "DROP TABLE meta.ingestion_checkpoints",
    "DROP TABLE meta.ingestion_runs",
    "DROP TABLE meta.data_sources",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
