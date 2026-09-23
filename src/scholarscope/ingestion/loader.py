"""Idempotent loading of transformed OpenAlex rows into PostgreSQL.

Dimensions are upserted (latest values win). A work's bridge rows are deleted and
re-inserted, so re-loading a work reflects its current authorships, topics and references.
The caller owns the transaction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import psycopg

from scholarscope.ingestion.transform import WorkRows

WORK_COLUMNS = (
    "work_id", "doi", "title", "abstract", "publication_date", "publication_year", "type", "language",
    "source_id", "is_oa", "oa_status", "cited_by_count", "fwci", "citation_percentile", "is_top_1pct",
    "is_top_10pct", "referenced_works_count", "is_retracted", "openalex_updated_at",
)
SOURCE_COLUMNS = ("source_id", "display_name", "type", "issn_l", "host_organization_name")
TOPIC_COLUMNS = (
    "topic_id", "display_name", "subfield_id", "subfield_name", "field_id", "field_name", "domain_id", "domain_name",
)
KEYWORD_COLUMNS = ("keyword_id", "display_name")
INSTITUTION_COLUMNS = ("institution_id", "display_name", "ror_id", "country_code", "type")
AUTHOR_COLUMNS = ("author_id", "display_name", "orcid")
WORK_AUTHOR_COLUMNS = (
    "work_id", "author_seq", "author_id", "raw_author_name", "author_position", "is_corresponding",
    "raw_affiliation_strings",
)

# Replaced wholesale per work on every load. Deleting bridge.work_authors rows cascades to
# bridge.authorship_institutions and bridge.authorship_countries.
_REPLACED_TABLES = (
    "bridge.work_authors",
    "bridge.work_topics",
    "bridge.work_keywords",
    "bridge.work_references",
    "core.work_yearly_citations",
)


def _placeholders(columns: Sequence[str]) -> str:
    return ", ".join(f"%({c})s" for c in columns)


def _upsert_sql(table: str, columns: Sequence[str]) -> str:
    key, *rest = columns
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({_placeholders(columns)}) "
        f"ON CONFLICT ({key}) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in rest)
    )


def _insert_sql(table: str, columns: Sequence[str]) -> str:
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({_placeholders(columns)}) "
        "ON CONFLICT DO NOTHING"
    )


UPSERT_COUNTRY = "INSERT INTO core.countries (country_code) VALUES (%(country_code)s) ON CONFLICT DO NOTHING"
UPSERT_SOURCE = _upsert_sql("core.sources", SOURCE_COLUMNS)
UPSERT_TOPIC = _upsert_sql("core.topics", TOPIC_COLUMNS)
UPSERT_KEYWORD = _upsert_sql("core.keywords", KEYWORD_COLUMNS)
UPSERT_INSTITUTION = _upsert_sql("core.institutions", INSTITUTION_COLUMNS)
UPSERT_AUTHOR = _upsert_sql("core.authors", AUTHOR_COLUMNS)
UPSERT_WORK = (
    f"INSERT INTO core.works ({', '.join(WORK_COLUMNS)}, first_run_id, last_run_id) "
    f"VALUES ({_placeholders(WORK_COLUMNS)}, %(run_id)s, %(run_id)s) "
    "ON CONFLICT (work_id) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in WORK_COLUMNS[1:])
    + ", last_run_id = EXCLUDED.last_run_id, loaded_at = now()"
)
INSERT_WORK_AUTHOR = _insert_sql("bridge.work_authors", WORK_AUTHOR_COLUMNS)
INSERT_AUTHORSHIP_INSTITUTION = _insert_sql(
    "bridge.authorship_institutions", ("work_id", "author_seq", "institution_id")
)
INSERT_AUTHORSHIP_COUNTRY = _insert_sql("bridge.authorship_countries", ("work_id", "author_seq", "country_code"))
INSERT_WORK_TOPIC = _insert_sql("bridge.work_topics", ("work_id", "topic_id", "score", "is_primary"))
INSERT_WORK_KEYWORD = _insert_sql("bridge.work_keywords", ("work_id", "keyword_id", "score"))
INSERT_WORK_REFERENCE = _insert_sql("bridge.work_references", ("work_id", "referenced_work_id"))
INSERT_YEARLY_CITATIONS = _insert_sql("core.work_yearly_citations", ("work_id", "year", "cited_by_count"))
UPSERT_RECALL_HIT = (
    "INSERT INTO meta.work_recall_hits (work_id, query_key, first_run_id, last_run_id, relevance_score) "
    "VALUES (%(work_id)s, %(query_key)s, %(run_id)s, %(run_id)s, %(relevance_score)s) "
    "ON CONFLICT (work_id, query_key) DO UPDATE SET "
    "last_run_id = EXCLUDED.last_run_id, relevance_score = EXCLUDED.relevance_score"
)


def _unique(rows: Iterable[dict], key: str) -> list[dict]:
    return list({row[key]: row for row in rows}.values())


def load_works(conn: psycopg.Connection, batch: Sequence[WorkRows], *, run_id: int, query_key: str) -> int:
    """Upsert one page of transformed works. Returns the number of works loaded."""
    if not batch:
        return 0
    work_ids = [rows.work["work_id"] for rows in batch]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_COUNTRY, [{"country_code": c} for c in sorted({c for r in batch for c in r.countries})])
        cur.executemany(UPSERT_SOURCE, _unique((r.source for r in batch if r.source), "source_id"))
        cur.executemany(UPSERT_TOPIC, _unique((t for r in batch for t in r.topics), "topic_id"))
        cur.executemany(UPSERT_KEYWORD, _unique((k for r in batch for k in r.keywords), "keyword_id"))
        cur.executemany(UPSERT_INSTITUTION, _unique((i for r in batch for i in r.institutions), "institution_id"))
        cur.executemany(UPSERT_AUTHOR, _unique((a for r in batch for a in r.authors), "author_id"))
        cur.executemany(UPSERT_WORK, [{**r.work, "run_id": run_id} for r in batch])

        for table in _REPLACED_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE work_id = ANY(%s)", (work_ids,))
        cur.executemany(INSERT_WORK_AUTHOR, [wa for r in batch for wa in r.work_authors])
        cur.executemany(INSERT_AUTHORSHIP_INSTITUTION, [ai for r in batch for ai in r.authorship_institutions])
        cur.executemany(INSERT_AUTHORSHIP_COUNTRY, [ac for r in batch for ac in r.authorship_countries])
        cur.executemany(INSERT_WORK_TOPIC, [wt for r in batch for wt in r.work_topics])
        cur.executemany(INSERT_WORK_KEYWORD, [wk for r in batch for wk in r.work_keywords])
        cur.executemany(
            INSERT_WORK_REFERENCE,
            [{"work_id": r.work["work_id"], "referenced_work_id": ref} for r in batch for ref in r.references],
        )
        cur.executemany(INSERT_YEARLY_CITATIONS, [yc for r in batch for yc in r.yearly_citations])
        cur.executemany(
            UPSERT_RECALL_HIT,
            [
                {"work_id": r.work["work_id"], "query_key": query_key, "run_id": run_id,
                 "relevance_score": r.relevance_score}
                for r in batch
            ],
        )
    return len(batch)
