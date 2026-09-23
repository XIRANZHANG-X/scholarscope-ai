"""core and bridge schemas for OpenAlex works, plus per-work recall provenance

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-22
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = [
    "CREATE SCHEMA core",
    "CREATE SCHEMA bridge",
    # ---- core dimensions -------------------------------------------------------------------
    """
    CREATE TABLE core.countries (
        country_code char(2) PRIMARY KEY,
        name         text
    )
    """,
    """
    CREATE TABLE core.sources (
        source_id              text PRIMARY KEY,
        display_name           text NOT NULL,
        type                   text,
        issn_l                 text,
        host_organization_name text
    )
    """,
    """
    CREATE TABLE core.topics (
        topic_id      text PRIMARY KEY,
        display_name  text NOT NULL,
        subfield_id   text,
        subfield_name text,
        field_id      text,
        field_name    text,
        domain_id     text,
        domain_name   text
    )
    """,
    """
    CREATE TABLE core.keywords (
        keyword_id   text PRIMARY KEY,
        display_name text NOT NULL
    )
    """,
    """
    CREATE TABLE core.institutions (
        institution_id text PRIMARY KEY,
        display_name   text NOT NULL,
        ror_id         text,
        country_code   char(2) REFERENCES core.countries,
        type           text
    )
    """,
    """
    CREATE TABLE core.authors (
        author_id    text PRIMARY KEY,
        display_name text NOT NULL,
        orcid        text
    )
    """,
    # ---- core facts ------------------------------------------------------------------------
    """
    CREATE TABLE core.works (
        work_id                text PRIMARY KEY CHECK (work_id ~ '^W[0-9]+$'),
        doi                    text,
        title                  text,
        abstract               text,
        publication_date       date NOT NULL,
        publication_year       smallint NOT NULL,
        type                   text NOT NULL,
        language               text,
        source_id              text REFERENCES core.sources,
        is_oa                  boolean,
        oa_status              text,
        cited_by_count         integer NOT NULL DEFAULT 0,
        fwci                   numeric,
        citation_percentile    numeric,
        is_top_1pct            boolean,
        is_top_10pct           boolean,
        referenced_works_count integer NOT NULL DEFAULT 0,
        is_retracted           boolean NOT NULL DEFAULT false,
        openalex_updated_at    timestamptz,
        first_run_id           bigint NOT NULL REFERENCES meta.ingestion_runs,
        last_run_id            bigint NOT NULL REFERENCES meta.ingestion_runs,
        loaded_at              timestamptz NOT NULL DEFAULT now()
    )
    """,
    "COMMENT ON COLUMN core.works.doi IS 'Lower-cased DOI without the https://doi.org/ prefix; not unique in OpenAlex'",
    "CREATE INDEX works_publication_date_idx ON core.works (publication_date)",
    "CREATE INDEX works_doi_idx ON core.works (doi)",
    "CREATE INDEX works_source_idx ON core.works (source_id)",
    """
    CREATE TABLE core.work_yearly_citations (
        work_id        text REFERENCES core.works ON DELETE CASCADE,
        year           smallint,
        cited_by_count integer NOT NULL,
        PRIMARY KEY (work_id, year)
    )
    """,
    # ---- bridge tables ---------------------------------------------------------------------
    """
    CREATE TABLE bridge.work_authors (
        work_id                 text REFERENCES core.works ON DELETE CASCADE,
        author_seq              smallint,
        author_id               text REFERENCES core.authors,
        raw_author_name         text NOT NULL,
        author_position         text,
        is_corresponding        boolean NOT NULL DEFAULT false,
        raw_affiliation_strings text[] NOT NULL DEFAULT '{}',
        PRIMARY KEY (work_id, author_seq)
    )
    """,
    "COMMENT ON COLUMN bridge.work_authors.author_id IS 'NULL when OpenAlex has not disambiguated the author'",
    "CREATE INDEX work_authors_author_idx ON bridge.work_authors (author_id)",
    """
    CREATE TABLE bridge.authorship_institutions (
        work_id        text,
        author_seq     smallint,
        institution_id text REFERENCES core.institutions,
        PRIMARY KEY (work_id, author_seq, institution_id),
        FOREIGN KEY (work_id, author_seq) REFERENCES bridge.work_authors ON DELETE CASCADE
    )
    """,
    "CREATE INDEX authorship_institutions_institution_idx ON bridge.authorship_institutions (institution_id)",
    """
    CREATE TABLE bridge.authorship_countries (
        work_id      text,
        author_seq   smallint,
        country_code char(2) REFERENCES core.countries,
        PRIMARY KEY (work_id, author_seq, country_code),
        FOREIGN KEY (work_id, author_seq) REFERENCES bridge.work_authors ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE bridge.work_topics (
        work_id    text REFERENCES core.works ON DELETE CASCADE,
        topic_id   text REFERENCES core.topics,
        score      real NOT NULL,
        is_primary boolean NOT NULL,
        PRIMARY KEY (work_id, topic_id)
    )
    """,
    "CREATE INDEX work_topics_topic_idx ON bridge.work_topics (topic_id)",
    """
    CREATE TABLE bridge.work_keywords (
        work_id    text REFERENCES core.works ON DELETE CASCADE,
        keyword_id text REFERENCES core.keywords,
        score      real NOT NULL,
        PRIMARY KEY (work_id, keyword_id)
    )
    """,
    """
    CREATE TABLE bridge.work_references (
        work_id            text REFERENCES core.works ON DELETE CASCADE,
        referenced_work_id text NOT NULL CHECK (referenced_work_id ~ '^W[0-9]+$'),
        PRIMARY KEY (work_id, referenced_work_id)
    )
    """,
    "COMMENT ON COLUMN bridge.work_references.referenced_work_id IS "
    "'No foreign key: most cited works are outside the corpus'",
    "CREATE INDEX work_references_referenced_idx ON bridge.work_references (referenced_work_id)",
    """
    CREATE VIEW bridge.work_institutions AS
    SELECT DISTINCT work_id, institution_id
    FROM bridge.authorship_institutions
    """,
    """
    CREATE VIEW bridge.author_affiliations AS
    SELECT wa.author_id,
           ai.institution_id,
           min(w.publication_year)   AS first_year,
           max(w.publication_year)   AS last_year,
           count(DISTINCT w.work_id) AS works_count
    FROM bridge.work_authors wa
    JOIN bridge.authorship_institutions ai USING (work_id, author_seq)
    JOIN core.works w USING (work_id)
    WHERE wa.author_id IS NOT NULL
    GROUP BY wa.author_id, ai.institution_id
    """,
    # ---- recall provenance ("why is this work in the corpus?", architecture §5.2) ----------
    """
    CREATE TABLE meta.work_recall_hits (
        work_id         text REFERENCES core.works ON DELETE CASCADE,
        query_key       text NOT NULL,
        first_run_id    bigint NOT NULL REFERENCES meta.ingestion_runs,
        last_run_id     bigint NOT NULL REFERENCES meta.ingestion_runs,
        relevance_score real,
        PRIMARY KEY (work_id, query_key)
    )
    """,
]

DOWNGRADE = [
    "DROP TABLE meta.work_recall_hits",
    "DROP SCHEMA bridge CASCADE",
    "DROP SCHEMA core CASCADE",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
