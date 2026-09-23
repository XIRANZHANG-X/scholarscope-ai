"""Enrichment flows: ROR organisations and affiliations, and World Bank country indicators.

Both record a run in `meta.ingestion_runs` exactly as OpenAlex ingestion does, so every external row
can be traced to the release or API call that produced it. `conn` must be in autocommit mode: each
unit of work is its own transaction.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
import psycopg

from scholarscope.external import loader, ror, worldbank
from scholarscope.external.ror_match import DEFAULT_MIN_SCORE, AffiliationMatch, match_affiliation
from scholarscope.ingestion import runs

log = logging.getLogger(__name__)

BATCH_SIZE = 500
# ROR asks for courtesy rather than enforcing a hard limit; ~5 requests/second stays well inside it.
MATCH_PAUSE_S = 0.2
# Matching thousands of strings takes minutes; cache each block so an interruption keeps the work.
FLUSH_EVERY = 100

DISTINCT_UNLINKED_AFFILIATIONS = """
SELECT DISTINCT btrim(raw.affiliation) AS affiliation
FROM bridge.work_authors author
CROSS JOIN LATERAL unnest(author.raw_affiliation_strings) AS raw(affiliation)
WHERE btrim(raw.affiliation) <> ''
  AND NOT EXISTS (SELECT 1 FROM bridge.authorship_institutions linked
                  WHERE linked.work_id = author.work_id AND linked.author_seq = author.author_seq)
  AND NOT EXISTS (SELECT 1 FROM external.affiliation_matches cached
                  WHERE cached.affiliation = btrim(raw.affiliation))
ORDER BY affiliation
"""


@dataclass(frozen=True)
class RorResult:
    run_id: int
    version: str
    organizations_loaded: int
    institutions_linked: int
    affiliations_matched: int
    affiliations_unmatched: int


@dataclass(frozen=True)
class WorldBankResult:
    run_id: int
    countries: int
    observations: int
    missing_values: int


def load_dump_subset(conn: psycopg.Connection, dump_path: Path, wanted: set[str], *, run_id: int) -> int:
    """Load exactly the organisations in `wanted` from the dump, in batches."""
    if not wanted:
        return 0
    loaded, batch = 0, []
    for row in ror.iter_csv_rows(dump_path):
        if ror.short_ror_id(row["id"]) not in wanted:
            continue
        batch.append(ror.parse_organization(row))
        if len(batch) >= BATCH_SIZE:
            with conn.transaction():
                loaded += loader.load_ror_organizations(conn, batch, run_id=run_id)
            batch = []
    if batch:
        with conn.transaction():
            loaded += loader.load_ror_organizations(conn, batch, run_id=run_id)
    return loaded


def match_unlinked_affiliations(
    conn: psycopg.Connection,
    http: httpx.Client,
    dump_path: Path,
    *,
    run_id: int,
    min_score: float = DEFAULT_MIN_SCORE,
    limit: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, int, int]:
    """Ask ROR about every distinct affiliation string OpenAlex left unlinked.

    Returns (matched, unmatched, organisations added). Results are cached per string — including a
    confident no-match, stored as a NULL ror_id — so a later run never pays for the same string
    twice. The cache is written every `FLUSH_EVERY` answers rather than at the end, because the loop
    runs for minutes and an interruption would otherwise throw away every API call it had made.
    Organisations the match points at are loaded from the dump before the cache references them,
    because the cache has a foreign key to `external.ror_organizations`.
    """
    affiliations = [row[0] for row in conn.execute(DISTINCT_UNLINKED_AFFILIATIONS).fetchall()]
    if limit is not None:
        affiliations = affiliations[:limit]
    if not affiliations:
        return 0, 0, 0

    matched = unmatched = added = 0
    for start in range(0, len(affiliations), FLUSH_EVERY):
        block = affiliations[start:start + FLUSH_EVERY]
        results: list[tuple[str, AffiliationMatch | None]] = []
        for index, affiliation in enumerate(block, start=start + 1):
            results.append((affiliation, match_affiliation(http, affiliation, min_score=min_score)))
            if index < len(affiliations):
                sleep(MATCH_PAUSE_S)
        just_matched, just_added = _cache_flush(conn, dump_path, results, run_id=run_id)
        matched += just_matched
        unmatched += sum(1 for _, match in results if match is None)
        added += just_added
        log.info("run %s: asked ROR about %d of %d affiliation strings",
                 run_id, start + len(block), len(affiliations))

    log.info("run %s: %d of %d affiliation strings matched (%d organisations added)",
             run_id, matched, len(affiliations), added)
    return matched, unmatched, added


def _cache_flush(
    conn: psycopg.Connection,
    dump_path: Path,
    results: Sequence[tuple[str, AffiliationMatch | None]],
    *,
    run_id: int,
) -> tuple[int, int]:
    """Load this flush's organisations, then cache its rows. Returns (matched, organisations added)."""
    referenced = {match.ror_id for _, match in results if match}
    added = load_dump_subset(conn, dump_path, loader.missing_ror_ids(conn, referenced), run_id=run_id)
    storable = _drop_matches_outside_the_dump(conn, results, run_id=run_id)
    with conn.transaction():
        loader.load_affiliation_matches(conn, storable, run_id=run_id)
    return sum(1 for _, match in storable if match), added


def _drop_matches_outside_the_dump(
    conn: psycopg.Connection, results: Sequence[tuple[str, AffiliationMatch | None]], *, run_id: int
) -> list[tuple[str, AffiliationMatch | None]]:
    """Drop matches naming ROR ids the dump does not hold: it is a monthly snapshot, the API is live.

    Both caches have a foreign key to `external.ror_organizations`, so one such row would fail the
    whole batch. They are skipped rather than recorded as a no-match — the string does match, we
    simply cannot reference it yet — which leaves a run against a newer dump free to retry them.
    """
    unknown = loader.missing_ror_ids(conn, {match.ror_id for _, match in results if match})
    if not unknown:
        return list(results)
    kept = [(key, match) for key, match in results if not (match and match.ror_id in unknown)]
    log.warning(
        "run %s: skipped %d match(es) naming %d ROR id(s) absent from this dump, which is older than "
        "ROR's data: %s", run_id, len(results) - len(kept), len(unknown), ", ".join(sorted(unknown))
    )
    return kept


def match_institutions_without_ror(
    conn: psycopg.Connection,
    http: httpx.Client,
    dump_path: Path,
    *,
    run_id: int,
    min_score: float = DEFAULT_MIN_SCORE,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, int]:
    """Match institutions OpenAlex gave no ROR id, by their display name. Returns (matched, added)."""
    pending = loader.unmatched_institutions(conn)
    if not pending:
        return 0, 0
    matches: list[tuple[str, AffiliationMatch]] = []
    for index, (institution_id, display_name) in enumerate(pending, start=1):
        match = match_affiliation(http, display_name, min_score=min_score)
        if match:
            matches.append((institution_id, match))
        if index < len(pending):
            sleep(MATCH_PAUSE_S)
    added = load_dump_subset(
        conn, dump_path, loader.missing_ror_ids(conn, {match.ror_id for _, match in matches}), run_id=run_id
    )
    matches = _drop_matches_outside_the_dump(conn, matches, run_id=run_id)
    with conn.transaction():
        for institution_id, match in matches:
            loader.link_crosswalk_match(conn, institution_id, match.ror_id, match.score, run_id=run_id)
    log.info("run %s: %d of %d unlinked institutions matched by name", run_id, len(matches), len(pending))
    return len(matches), added


def enrich_ror(
    conn: psycopg.Connection,
    http: httpx.Client,
    *,
    dump_dir: Path,
    dump_path: Path | None = None,
    match_affiliations: bool = True,
    min_score: float = DEFAULT_MIN_SCORE,
    max_affiliations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> RorResult:
    """Load the corpus's ROR records, link institutions, then match the affiliations left over."""
    if dump_path is None:
        release = ror.latest_release(http)
        dump_path, digest = ror.download(http, release, dump_dir)
        version, doi = release.version, release.doi
    else:
        digest, doi = ror.sha256_of(dump_path), ""
        version = dump_path.name.removesuffix("-ror-data.zip")

    run_id = runs.start_run(
        conn,
        "dump",
        {"version": version, "doi": doi, "file_name": dump_path.name, "sha256": digest, "min_score": min_score},
        source_id="ror",
    )
    log.info("ROR run %s: release %s (sha256 %s…)", run_id, version, digest[:12])

    try:
        organizations = load_dump_subset(conn, dump_path, loader.wanted_ror_ids(conn), run_id=run_id)
        with conn.transaction():
            linked = loader.link_crosswalk_from_openalex(conn, run_id=run_id)

        matched = unmatched = 0
        if match_affiliations:
            by_name, added = match_institutions_without_ror(
                conn, http, dump_path, run_id=run_id, min_score=min_score, sleep=sleep
            )
            organizations += added
            linked += by_name

            matched, unmatched, added = match_unlinked_affiliations(
                conn, http, dump_path, run_id=run_id, min_score=min_score,
                limit=max_affiliations, sleep=sleep,
            )
            organizations += added
    except Exception as exc:
        runs.finish_run(conn, run_id, "failed", repr(exc))
        raise

    runs.finish_run(conn, run_id, "succeeded")
    log.info("ROR run %s succeeded: %d organisations, %d institutions linked", run_id, organizations, linked)
    return RorResult(run_id, version, organizations, linked, matched, unmatched)


def enrich_worldbank(
    conn: psycopg.Connection, http: httpx.Client, *, from_year: int, to_year: int
) -> WorldBankResult:
    """Refresh country metadata and every indicator series in `worldbank.INDICATORS`."""
    run_id = runs.start_run(
        conn,
        "indicators",
        {"indicators": sorted(worldbank.INDICATORS), "from_year": from_year, "to_year": to_year},
        source_id="worldbank",
    )
    log.info("World Bank run %s: %d indicators, %d-%d", run_id, len(worldbank.INDICATORS), from_year, to_year)

    try:
        profiles = worldbank.fetch_countries(http)
        with conn.transaction():
            countries = loader.load_country_profiles(conn, profiles, run_id=run_id)
            loader.load_indicator_catalogue(conn, worldbank.INDICATORS)

        observations = missing = 0
        for indicator_code in worldbank.INDICATORS:
            series = worldbank.fetch_indicator(http, indicator_code, from_year=from_year, to_year=to_year)
            with conn.transaction():
                stored, nulls = loader.load_observations(conn, series, run_id=run_id)
            observations += stored
            missing += nulls
            log.info("World Bank run %s: %s -> %d observations stored", run_id, indicator_code, stored)
    except Exception as exc:
        runs.finish_run(conn, run_id, "failed", repr(exc))
        raise

    runs.finish_run(conn, run_id, "succeeded")
    return WorldBankResult(run_id, countries, observations, missing)
