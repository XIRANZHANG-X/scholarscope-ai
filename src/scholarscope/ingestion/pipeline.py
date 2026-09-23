"""OpenAlex ingestion: fetch -> raw cache -> transform -> load, one page per transaction, resumable."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date

import psycopg

from scholarscope.ingestion import runs
from scholarscope.ingestion.loader import load_works
from scholarscope.ingestion.openalex_client import BudgetExhaustedError, OpenAlexClient
from scholarscope.ingestion.raw_cache import RawCache
from scholarscope.ingestion.recall import RecallConfig, build_filter
from scholarscope.ingestion.transform import transform_work

log = logging.getLogger(__name__)

MAX_PER_PAGE = 100


@dataclass(frozen=True)
class Profile:
    name: str
    max_works_per_query: int | None  # None = every work the query matches
    max_cost_usd: float              # safety stop per invocation; the run stays resumable
    sort: str                        # OpenAlex sort for cursor paging


# Relevance scores drift slightly between requests (measured 2026-09-22: 5 works repeated in
# 1 000), so relevance-ordered paging can repeat or skip works at page boundaries. Capped subsets
# keep relevance order ("the most relevant N"); the full corpus pages by publication date, whose
# cursor breaks ties by work ID and returned 1 000 of 1 000 unique works in the same test.
BY_RELEVANCE = "relevance_score:desc"
BY_DATE = "publication_date:asc"


# Architecture §5.3. config/recall.toml holds one query (RAG, Computer Science: 15,921 works on
# 2026-09-22), so smoke = 1 000 works, demo = 5 000 and standard = the whole corpus (~$0.16).
PROFILES = {
    "smoke": Profile("smoke", 1000, 0.05, BY_RELEVANCE),
    "demo": Profile("demo", 5000, 0.50, BY_RELEVANCE),
    "standard": Profile("standard", None, 0.90, BY_DATE),
}


@dataclass(frozen=True)
class IngestResult:
    run_id: int
    status: str
    works_fetched: int
    cost_usd: float
    message: str | None


def ingest(
    conn: psycopg.Connection,
    client: OpenAlexClient,
    cache: RawCache,
    recall: RecallConfig,
    profile: Profile,
    *,
    to_date: date,
) -> IngestResult:
    """Start a new run. `conn` must be in autocommit mode: each page is its own transaction."""
    params = {"profile": asdict(profile), "to_date": to_date.isoformat(), "recall": recall.to_dict()}
    run_id = runs.start_run(conn, profile.name, params)
    log.info("started ingestion run %s (profile %s, to_date %s)", run_id, profile.name, to_date)
    return _execute(conn, client, cache, run_id, recall, profile, to_date)


def resume(conn: psycopg.Connection, client: OpenAlexClient, cache: RawCache, run_id: int) -> IngestResult:
    """Continue a run from its checkpoints, with the exact filters and limits it was started with."""
    run = runs.get_run(conn, run_id)
    recall = RecallConfig.from_dict(run.params["recall"])
    to_date = date.fromisoformat(run.params["to_date"])
    profile = Profile(**run.params["profile"])
    runs.mark_running(conn, run_id)
    log.info("resuming ingestion run %s", run_id)
    return _execute(conn, client, cache, run_id, recall, profile, to_date)


def _execute(
    conn: psycopg.Connection,
    client: OpenAlexClient,
    cache: RawCache,
    run_id: int,
    recall: RecallConfig,
    profile: Profile,
    to_date: date,
) -> IngestResult:
    spent = 0.0
    fetched = 0

    def finish(status: str, message: str | None = None) -> IngestResult:
        runs.finish_run(conn, run_id, status, message)
        log.info("run %s %s: %d works, $%.4f. %s", run_id, status, fetched, spent, message or "")
        return IngestResult(run_id, status, fetched, spent, message)

    try:
        for query in recall.queries:
            checkpoint = runs.get_checkpoint(conn, run_id, query.key)
            filter_ = build_filter(recall, query, to_date=to_date)
            cap = profile.max_works_per_query
            while not checkpoint.is_exhausted and (cap is None or checkpoint.works_fetched < cap):
                if spent >= profile.max_cost_usd:
                    return finish("partial", f"cost limit ${profile.max_cost_usd} reached; resume with --resume {run_id}")
                per_page = MAX_PER_PAGE if cap is None else min(MAX_PER_PAGE, cap - checkpoint.works_fetched)
                page = client.fetch_works_page(
                    filter_, cursor=checkpoint.next_cursor, per_page=per_page, sort=profile.sort
                )
                page_no = checkpoint.pages_done + 1
                cache.write_page(run_id, query.key, page_no, page.raw)
                batch = [transform_work(work) for work in page.results]
                checkpoint = runs.Checkpoint(
                    query.key,
                    next_cursor=page.next_cursor,
                    pages_done=page_no,
                    works_fetched=checkpoint.works_fetched + len(batch),
                    is_exhausted=page.next_cursor is None or not batch,
                )
                with conn.transaction():
                    load_works(conn, batch, run_id=run_id, query_key=query.key)
                    runs.record_page(conn, run_id, checkpoint, cost_usd=page.cost_usd, records=len(batch))
                spent += page.cost_usd
                fetched += len(batch)
                log.info("%s page %d: %d works (%d of %d matched)", query.key, page_no, len(batch),
                         checkpoint.works_fetched, page.count)
    except BudgetExhaustedError as exc:
        return finish("partial", f"{exc}; resume with --resume {run_id}")
    except Exception as exc:
        finish("failed", repr(exc))
        raise
    return finish("succeeded")
