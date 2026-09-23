"""external schema: ROR organisations and relationships, institution crosswalk, World Bank country data

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = [
    "CREATE SCHEMA external",
    """
    INSERT INTO meta.data_sources (source_id, name, url, license, notes) VALUES
        ('ror', 'ROR (Research Organization Registry)', 'https://ror.org', 'CC0 1.0',
         'Versioned data dump from Zenodo (community ror-data), plus the affiliation matching API.'),
        ('worldbank', 'World Bank Open Data', 'https://data.worldbank.org', 'CC BY 4.0',
         'Country metadata and development indicators via the v2 REST API.')
    """,
    # ---- ROR ---------------------------------------------------------------------------------
    """
    CREATE TABLE external.ror_organizations (
        ror_id           text PRIMARY KEY CHECK (ror_id ~ '^0[0-9a-z]{8}$'),
        display_name     text NOT NULL,
        status           text NOT NULL CHECK (status IN ('active', 'inactive', 'withdrawn')),
        established      smallint,
        types            text[] NOT NULL DEFAULT '{}',
        aliases          text[] NOT NULL DEFAULT '{}',
        acronyms         text[] NOT NULL DEFAULT '{}',
        country_code     char(2),
        country_name     text,
        subdivision_name text,
        city             text,
        latitude         double precision,
        longitude        double precision,
        continent_code   text,
        website          text,
        wikidata_id      text,
        grid_id          text,
        run_id           bigint NOT NULL REFERENCES meta.ingestion_runs,
        loaded_at        timestamptz NOT NULL DEFAULT now()
    )
    """,
    "COMMENT ON TABLE external.ror_organizations IS "
    "'Subset of the ROR dump: the organisations referenced by core.institutions'",
    "CREATE INDEX ror_organizations_country_idx ON external.ror_organizations (country_code)",
    """
    CREATE TABLE external.ror_relationships (
        ror_id            text REFERENCES external.ror_organizations ON DELETE CASCADE,
        related_ror_id    text NOT NULL,
        relationship_type text NOT NULL
            CHECK (relationship_type IN ('parent', 'child', 'related', 'successor', 'predecessor')),
        PRIMARY KEY (ror_id, related_ror_id, relationship_type)
    )
    """,
    "COMMENT ON COLUMN external.ror_relationships.related_ror_id IS "
    "'No foreign key: the other side is often outside the loaded subset'",
    """
    CREATE TABLE external.institution_crosswalk (
        institution_id text PRIMARY KEY REFERENCES core.institutions ON DELETE CASCADE,
        ror_id         text NOT NULL REFERENCES external.ror_organizations,
        match_method   text NOT NULL CHECK (match_method IN ('openalex', 'affiliation_string')),
        match_score    real,
        run_id         bigint NOT NULL REFERENCES meta.ingestion_runs,
        matched_at     timestamptz NOT NULL DEFAULT now()
    )
    """,
    "COMMENT ON COLUMN external.institution_crosswalk.match_score IS "
    "'NULL when OpenAlex supplied the ROR id; the ROR matching score when we matched a name'",
    "CREATE INDEX institution_crosswalk_ror_idx ON external.institution_crosswalk (ror_id)",
    """
    CREATE TABLE external.affiliation_matches (
        affiliation text PRIMARY KEY,
        ror_id      text REFERENCES external.ror_organizations,
        match_score real,
        run_id      bigint NOT NULL REFERENCES meta.ingestion_runs,
        matched_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    "COMMENT ON TABLE external.affiliation_matches IS "
    "'Raw affiliation string -> ROR, for authorships OpenAlex left unlinked. One row per distinct "
    "string so a repeated affiliation costs one API call; ror_id NULL records a confident no-match'",
    # ---- World Bank --------------------------------------------------------------------------
    """
    CREATE TABLE external.country_profiles (
        country_code  char(2) PRIMARY KEY REFERENCES core.countries,
        iso3_code     char(3) NOT NULL,
        name          text NOT NULL,
        region        text,
        income_level  text,
        capital_city  text,
        latitude      double precision,
        longitude     double precision,
        run_id        bigint NOT NULL REFERENCES meta.ingestion_runs
    )
    """,
    """
    CREATE TABLE external.indicators (
        indicator_code text PRIMARY KEY,
        name           text NOT NULL
    )
    """,
    """
    CREATE TABLE external.country_indicators (
        country_code   char(2) NOT NULL REFERENCES core.countries,
        indicator_code text NOT NULL REFERENCES external.indicators,
        year           smallint NOT NULL,
        value          double precision,
        run_id         bigint NOT NULL REFERENCES meta.ingestion_runs,
        PRIMARY KEY (country_code, indicator_code, year)
    )
    """,
    "COMMENT ON COLUMN external.country_indicators.value IS "
    "'NULL means the World Bank has no observation; R&D series lag ~2 years behind the corpus'",
    "CREATE INDEX country_indicators_series_idx ON external.country_indicators (indicator_code, year)",
    # ---- derived view (architecture §7.3) ------------------------------------------------------
    """
    CREATE VIEW bridge.institution_relationships AS
    SELECT source.institution_id,
           target.institution_id AS related_institution_id,
           relationship.relationship_type
    FROM external.ror_relationships relationship
    JOIN external.institution_crosswalk source ON source.ror_id = relationship.ror_id
    JOIN external.institution_crosswalk target ON target.ror_id = relationship.related_ror_id
    """,
    "COMMENT ON VIEW bridge.institution_relationships IS "
    "'ROR parent/child/related links, restricted to institution pairs that both appear in the corpus'",
    """
    CREATE VIEW bridge.authorship_ror_institutions AS
    SELECT author.work_id,
           author.author_seq,
           matched.ror_id
    FROM bridge.work_authors author
    CROSS JOIN LATERAL unnest(author.raw_affiliation_strings) AS raw(affiliation)
    JOIN external.affiliation_matches matched ON matched.affiliation = btrim(raw.affiliation)
    WHERE matched.ror_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM bridge.authorship_institutions linked
                      WHERE linked.work_id = author.work_id AND linked.author_seq = author.author_seq)
    """,
    "COMMENT ON VIEW bridge.authorship_ror_institutions IS "
    "'Affiliations recovered from raw strings; only for authorships OpenAlex did not link itself'",
    """
    CREATE VIEW bridge.work_countries AS
    SELECT DISTINCT work_id, country_code FROM bridge.authorship_countries
    UNION
    SELECT DISTINCT recovered.work_id, organization.country_code
    FROM bridge.authorship_ror_institutions recovered
    JOIN external.ror_organizations organization ON organization.ror_id = recovered.ror_id
    WHERE organization.country_code IS NOT NULL
    """,
    "COMMENT ON VIEW bridge.work_countries IS "
    "'Best-effort country attribution: OpenAlex authorship countries plus ROR-recovered affiliations'",
]

DOWNGRADE = [
    "DROP VIEW bridge.work_countries",
    "DROP VIEW bridge.authorship_ror_institutions",
    "DROP VIEW bridge.institution_relationships",
    "DROP SCHEMA external CASCADE",
    "DELETE FROM meta.data_sources WHERE source_id IN ('ror', 'worldbank')",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
