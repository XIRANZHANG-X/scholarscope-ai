"""Ingestion run bookkeeping in meta.ingestion_runs and per-query resume checkpoints."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

SOURCE_ID = "openalex"


@dataclass(frozen=True)
class Checkpoint:
    query_key: str
    next_cursor: str | None = "*"
    pages_done: int = 0
    works_fetched: int = 0
    is_exhausted: bool = False


@dataclass(frozen=True)
class RunInfo:
    run_id: int
    profile: str
    params: dict
    status: str


def start_run(conn: psycopg.Connection, profile: str, params: dict) -> int:
    row = conn.execute(
        "INSERT INTO meta.ingestion_runs (source_id, profile, params, status) "
        "VALUES (%s, %s, %s, 'running') RETURNING run_id",
        (SOURCE_ID, profile, Jsonb(params)),
    ).fetchone()
    return row[0]


def get_run(conn: psycopg.Connection, run_id: int) -> RunInfo:
    row = conn.execute(
        "SELECT run_id, profile, params, status FROM meta.ingestion_runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"ingestion run {run_id} does not exist")
    return RunInfo(*row)


def mark_running(conn: psycopg.Connection, run_id: int) -> None:
    conn.execute(
        "UPDATE meta.ingestion_runs SET status = 'running', finished_at = NULL, message = NULL WHERE run_id = %s",
        (run_id,),
    )


def finish_run(conn: psycopg.Connection, run_id: int, status: str, message: str | None = None) -> None:
    conn.execute(
        "UPDATE meta.ingestion_runs SET status = %s, finished_at = now(), message = %s WHERE run_id = %s",
        (status, message, run_id),
    )


def get_checkpoint(conn: psycopg.Connection, run_id: int, query_key: str) -> Checkpoint:
    row = conn.execute(
        "SELECT next_cursor, pages_done, works_fetched, is_exhausted FROM meta.ingestion_checkpoints "
        "WHERE run_id = %s AND query_key = %s",
        (run_id, query_key),
    ).fetchone()
    return Checkpoint(query_key) if row is None else Checkpoint(query_key, *row)


def record_page(conn: psycopg.Connection, run_id: int, checkpoint: Checkpoint, *, cost_usd: float, records: int) -> None:
    """Persist the checkpoint reached after a page and add the page to the run's counters."""
    conn.execute(
        "INSERT INTO meta.ingestion_checkpoints "
        "(run_id, query_key, next_cursor, pages_done, works_fetched, is_exhausted) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (run_id, query_key) DO UPDATE SET next_cursor = EXCLUDED.next_cursor, "
        "pages_done = EXCLUDED.pages_done, works_fetched = EXCLUDED.works_fetched, "
        "is_exhausted = EXCLUDED.is_exhausted, updated_at = now()",
        (run_id, checkpoint.query_key, checkpoint.next_cursor, checkpoint.pages_done,
         checkpoint.works_fetched, checkpoint.is_exhausted),
    )
    conn.execute(
        "UPDATE meta.ingestion_runs SET requests_made = requests_made + 1, "
        "records_fetched = records_fetched + %s, cost_usd = cost_usd + %s WHERE run_id = %s",
        (records, cost_usd, run_id),
    )
