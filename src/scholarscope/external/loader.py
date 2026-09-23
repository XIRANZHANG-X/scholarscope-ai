"""Idempotent writes into the `external` schema. The caller owns the transaction."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import psycopg

from scholarscope.external.ror import OrganizationRows
from scholarscope.external.worldbank import CountryProfile

ROR_COLUMNS = (
    "ror_id", "display_name", "status", "established", "types", "aliases", "acronyms", "country_code",
    "country_name", "subdivision_name", "city", "latitude", "longitude", "continent_code", "website",
    "wikidata_id", "grid_id",
)
PROFILE_COLUMNS = (
    "country_code", "iso3_code", "name", "region", "income_level", "capital_city", "latitude", "longitude",
)


def _upsert_sql(table: str, columns: Sequence[str], key: str) -> str:
    placeholders = ", ".join(f"%({column})s" for column in columns)
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns if column != key)
    return (
        f"INSERT INTO {table} ({', '.join(columns)}, run_id) VALUES ({placeholders}, %(run_id)s) "
        f"ON CONFLICT ({key}) DO UPDATE SET {updates}, run_id = EXCLUDED.run_id"
    )


UPSERT_ROR_ORGANIZATION = _upsert_sql("external.ror_organizations", ROR_COLUMNS, "ror_id") + ", loaded_at = now()"
INSERT_ROR_RELATIONSHIP = (
    "INSERT INTO external.ror_relationships (ror_id, related_ror_id, relationship_type) "
    "VALUES (%(ror_id)s, %(related_ror_id)s, %(relationship_type)s) ON CONFLICT DO NOTHING"
)
UPSERT_COUNTRY_PROFILE = _upsert_sql("external.country_profiles", PROFILE_COLUMNS, "country_code")
UPSERT_COUNTRY = (
    "INSERT INTO core.countries (country_code, name) VALUES (%(country_code)s, %(name)s) "
    "ON CONFLICT (country_code) DO UPDATE SET name = EXCLUDED.name"
)
UPSERT_INDICATOR = (
    "INSERT INTO external.indicators (indicator_code, name) VALUES (%(indicator_code)s, %(name)s) "
    "ON CONFLICT (indicator_code) DO UPDATE SET name = EXCLUDED.name"
)
UPSERT_OBSERVATION = (
    "INSERT INTO external.country_indicators (country_code, indicator_code, year, value, run_id) "
    "VALUES (%(country_code)s, %(indicator_code)s, %(year)s, %(value)s, %(run_id)s) "
    "ON CONFLICT (country_code, indicator_code, year) DO UPDATE SET "
    "value = EXCLUDED.value, run_id = EXCLUDED.run_id"
)
# OpenAlex already carries a ROR id for most institutions; that is the highest-confidence link.
INSERT_CROSSWALK_FROM_OPENALEX = (
    "INSERT INTO external.institution_crosswalk (institution_id, ror_id, match_method, match_score, run_id) "
    "SELECT institution.institution_id, institution.ror_id, 'openalex', NULL, %(run_id)s "
    "FROM core.institutions institution "
    "JOIN external.ror_organizations organization ON organization.ror_id = institution.ror_id "
    "ON CONFLICT (institution_id) DO UPDATE SET ror_id = EXCLUDED.ror_id, match_method = 'openalex', "
    "match_score = NULL, run_id = EXCLUDED.run_id, matched_at = now()"
)
UPSERT_CROSSWALK_MATCH = (
    "INSERT INTO external.institution_crosswalk (institution_id, ror_id, match_method, match_score, run_id) "
    "VALUES (%(institution_id)s, %(ror_id)s, 'affiliation_string', %(match_score)s, %(run_id)s) "
    "ON CONFLICT (institution_id) DO UPDATE SET ror_id = EXCLUDED.ror_id, "
    "match_method = 'affiliation_string', match_score = EXCLUDED.match_score, run_id = EXCLUDED.run_id, "
    "matched_at = now()"
)

WANTED_ROR_IDS = (
    "SELECT ror_id FROM core.institutions WHERE ror_id IS NOT NULL "
    "UNION SELECT ror_id FROM external.affiliation_matches WHERE ror_id IS NOT NULL "
    "UNION SELECT ror_id FROM external.institution_crosswalk"
)
UNMATCHED_INSTITUTIONS = (
    "SELECT institution.institution_id, institution.display_name FROM core.institutions institution "
    "WHERE NOT EXISTS (SELECT 1 FROM external.institution_crosswalk crosswalk "
    "                  WHERE crosswalk.institution_id = institution.institution_id) "
    "ORDER BY institution.institution_id"
)


def wanted_ror_ids(conn: psycopg.Connection) -> set[str]:
    """Every ROR id the corpus references: OpenAlex's, plus the ones matching itself pulled in.

    The matched ones have to be here too, otherwise an organisation discovered by affiliation or
    name matching is loaded once and never refreshed by a later release.
    """
    return {row[0] for row in conn.execute(WANTED_ROR_IDS).fetchall()}


def unmatched_institutions(conn: psycopg.Connection, limit: int | None = None) -> list[tuple[str, str]]:
    """(institution_id, display_name) for institutions with no crosswalk row yet."""
    query = UNMATCHED_INSTITUTIONS + (f" LIMIT {int(limit)}" if limit else "")
    return [(row[0], row[1]) for row in conn.execute(query).fetchall()]


def load_ror_organizations(conn: psycopg.Connection, batch: Sequence[OrganizationRows], *, run_id: int) -> int:
    """Upsert organisations and replace their relationship rows."""
    if not batch:
        return 0
    ror_ids = [rows.organization["ror_id"] for rows in batch]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_ROR_ORGANIZATION, [{**rows.organization, "run_id": run_id} for rows in batch])
        cur.execute("DELETE FROM external.ror_relationships WHERE ror_id = ANY(%s)", (ror_ids,))
        cur.executemany(INSERT_ROR_RELATIONSHIP, [row for rows in batch for row in rows.relationships])
    return len(batch)


def link_crosswalk_from_openalex(conn: psycopg.Connection, *, run_id: int) -> int:
    """Link every institution whose OpenAlex ROR id is present in the loaded ROR subset."""
    with conn.cursor() as cur:
        cur.execute(INSERT_CROSSWALK_FROM_OPENALEX, {"run_id": run_id})
        return cur.rowcount


def link_crosswalk_match(
    conn: psycopg.Connection, institution_id: str, ror_id: str, score: float, *, run_id: int
) -> None:
    conn.execute(
        UPSERT_CROSSWALK_MATCH,
        {"institution_id": institution_id, "ror_id": ror_id, "match_score": score, "run_id": run_id},
    )


def load_country_profiles(conn: psycopg.Connection, profiles: Sequence[CountryProfile], *, run_id: int) -> int:
    """Upsert the country dimension (name included) and the World Bank profile rows."""
    if not profiles:
        return 0
    rows = [
        {
            "country_code": profile.country_code,
            "iso3_code": profile.iso3_code,
            "name": profile.name,
            "region": profile.region,
            "income_level": profile.income_level,
            "capital_city": profile.capital_city,
            "latitude": profile.latitude,
            "longitude": profile.longitude,
            "run_id": run_id,
        }
        for profile in profiles
    ]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_COUNTRY, rows)
        cur.executemany(UPSERT_COUNTRY_PROFILE, rows)
    return len(rows)


def load_indicator_catalogue(conn: psycopg.Connection, indicators: dict[str, str]) -> int:
    rows = [{"indicator_code": code, "name": name} for code, name in indicators.items()]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_INDICATOR, rows)
    return len(rows)


def load_observations(
    conn: psycopg.Connection, observations: Sequence[dict], *, run_id: int
) -> tuple[int, int]:
    """Store observations for countries we know; the API also reports codes outside core.countries.

    Returns (stored, NULL values among the stored rows) so callers report both numbers over the same
    population: the ~78 aggregate entities the API mixes in are neither stored nor counted.
    """
    if not observations:
        return 0, 0
    known = {row[0] for row in conn.execute("SELECT country_code FROM core.countries").fetchall()}
    rows = [{**observation, "run_id": run_id} for observation in observations if observation["country_code"] in known]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_OBSERVATION, rows)
    return len(rows), sum(1 for row in rows if row["value"] is None)


UPSERT_AFFILIATION_MATCH = (
    "INSERT INTO external.affiliation_matches (affiliation, ror_id, match_score, run_id) "
    "VALUES (%(affiliation)s, %(ror_id)s, %(match_score)s, %(run_id)s) "
    "ON CONFLICT (affiliation) DO UPDATE SET ror_id = EXCLUDED.ror_id, "
    "match_score = EXCLUDED.match_score, run_id = EXCLUDED.run_id, matched_at = now()"
)


def missing_ror_ids(conn: psycopg.Connection, ror_ids: Iterable[str]) -> set[str]:
    """Of `ror_ids`, the ones not yet in external.ror_organizations."""
    wanted = set(ror_ids)
    if not wanted:
        return set()
    present = conn.execute(
        "SELECT ror_id FROM external.ror_organizations WHERE ror_id = ANY(%s)", (sorted(wanted),)
    ).fetchall()
    return wanted - {row[0] for row in present}


def load_affiliation_matches(conn: psycopg.Connection, results: Sequence[tuple[str, object]], *, run_id: int) -> int:
    """Cache one row per affiliation string; a confident no-match is stored with a NULL ror_id."""
    rows = [
        {
            "affiliation": affiliation,
            "ror_id": getattr(match, "ror_id", None),
            "match_score": getattr(match, "score", None),
            "run_id": run_id,
        }
        for affiliation, match in results
    ]
    if rows:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_AFFILIATION_MATCH, rows)
    return len(rows)
