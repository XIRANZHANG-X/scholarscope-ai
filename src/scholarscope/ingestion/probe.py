"""Scope probe: how many works each recall query matches, by year and by type, before any bulk download.

Group-by calls cost $0.0001 each (2026-09-22), so a full probe of 8 queries costs well under $0.01.
"""

from __future__ import annotations

from datetime import date, datetime

import psycopg

from scholarscope.ingestion.openalex_client import OpenAlexClient
from scholarscope.ingestion.recall import CS_FIELD_ID, RecallConfig, build_filter

FIELD_SCOPES = {"all": (), "cs": (CS_FIELD_ID,)}
# The year breakdown uses the configured type filter; the type breakdown drops it to show what is excluded.
DIMENSIONS = {"publication_year": True, "type": False}

INSERT_PROBE = (
    "INSERT INTO meta.recall_probes "
    "(probed_at, query_key, theme, field_scope, dimension, bucket, works_count, filter) "
    "VALUES (%(probed_at)s, %(query_key)s, %(theme)s, %(field_scope)s, %(dimension)s, %(bucket)s, "
    "%(works_count)s, %(filter)s)"
)

SUMMARY_SQL = """
SELECT query_key,
       theme,
       sum(works_count) FILTER (WHERE field_scope = 'all') AS all_fields,
       sum(works_count) FILTER (WHERE field_scope = 'cs')  AS computer_science
FROM meta.recall_probes
WHERE probed_at = %s AND dimension = 'publication_year'
GROUP BY query_key, theme
ORDER BY theme, query_key
"""


def run_probe(
    conn: psycopg.Connection, client: OpenAlexClient, recall: RecallConfig, *, to_date: date, probed_at: datetime
) -> float:
    """Store bucket counts in meta.recall_probes; returns the API cost in USD."""
    rows: list[dict] = []
    cost = 0.0
    for query in recall.queries:
        for scope, field_ids in FIELD_SCOPES.items():
            for dimension, include_types in DIMENSIONS.items():
                filter_ = build_filter(recall, query, to_date=to_date, field_ids=field_ids, include_types=include_types)
                groups, call_cost = client.group_works(filter_, dimension)
                cost += call_cost
                rows += [
                    {"probed_at": probed_at, "query_key": query.key, "theme": query.theme, "field_scope": scope,
                     "dimension": dimension, "bucket": g.key, "works_count": g.count, "filter": filter_}
                    for g in groups
                ]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(INSERT_PROBE, rows)
    return cost


def probe_summary(conn: psycopg.Connection, probed_at: datetime) -> list[tuple[str, str, int, int]]:
    """(query_key, theme, all_fields, computer_science) totals for one probe, via SQL."""
    return conn.execute(SUMMARY_SQL, (probed_at,)).fetchall()
