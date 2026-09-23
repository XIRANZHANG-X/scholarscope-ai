"""Command-line entry point: `uv run scholarscope <command>`."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg

from scholarscope import db
from scholarscope.config import Settings
from scholarscope.external import pipeline as external_pipeline
from scholarscope.external.ror import RorDumpError
from scholarscope.external.ror_match import DEFAULT_MIN_SCORE
from scholarscope.external.worldbank import WorldBankError
from scholarscope.ingestion import pipeline, probe
from scholarscope.ingestion.openalex_client import OpenAlexClient, OpenAlexError
from scholarscope.ingestion.raw_cache import RawCache
from scholarscope.ingestion.recall import load_recall_config
from scholarscope.quality.checks import run_quality_checks

DEFAULT_RECALL = db.PROJECT_ROOT / "config" / "recall.toml"
USER_AGENT = "ScholarScopeAI/0.1"


def _plain_http(http: httpx.Client | None) -> httpx.Client:
    """A client for the external APIs (ROR, Zenodo, World Bank); each module uses absolute URLs."""
    if http is not None:
        return http
    return httpx.Client(timeout=httpx.Timeout(120.0), headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scholarscope", description="ScholarScope AI data pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="upgrade the database schema to the latest revision")

    p_probe = sub.add_parser("probe", help="count matching works per recall query (by year and type)")
    p_probe.add_argument("--recall", type=Path, default=DEFAULT_RECALL)

    p_ingest = sub.add_parser("ingest", help="download works from OpenAlex into PostgreSQL")
    mode = p_ingest.add_mutually_exclusive_group(required=True)
    mode.add_argument("--profile", choices=sorted(pipeline.PROFILES))
    mode.add_argument("--resume", type=int, metavar="RUN_ID")
    p_ingest.add_argument("--recall", type=Path, default=DEFAULT_RECALL)

    p_ror = sub.add_parser("ror", help="load the corpus's ROR organisations and match loose affiliations")
    p_ror.add_argument("--dump", type=Path, help="use this ROR dump instead of downloading the newest release")
    p_ror.add_argument("--no-match", action="store_true", help="skip ROR affiliation matching entirely")
    p_ror.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE, help="lowest accepted match score")
    p_ror.add_argument("--max-affiliations", type=int, help="stop after this many affiliation strings")

    p_worldbank = sub.add_parser("worldbank", help="refresh World Bank country profiles and indicators")
    p_worldbank.add_argument("--from-year", type=int, default=2019)
    p_worldbank.add_argument("--to-year", type=int, help="defaults to the current year")

    p_quality = sub.add_parser("quality", help="run data-quality checks and record them")
    p_quality.add_argument("--run", type=int, metavar="RUN_ID", help="ingestion run the checks belong to")
    return parser


def _client(settings: Settings, http: httpx.Client | None) -> OpenAlexClient:
    if http is None:
        http = httpx.Client(
            base_url=settings.openalex_base_url, timeout=httpx.Timeout(30.0), headers={"User-Agent": USER_AGENT}
        )
    key = settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None
    return OpenAlexClient(http, key)


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None, http: httpx.Client | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request is noise
    settings = settings or Settings()

    if args.command == "migrate":
        db.migrate(settings)
        print("database schema is at head")
        return 0

    with db.connect(settings, autocommit=True) as conn:
        if args.command == "probe":
            probed_at = datetime.now(UTC)
            cost = probe.run_probe(
                conn, _client(settings, http), load_recall_config(args.recall),
                to_date=probed_at.date(), probed_at=probed_at,
            )
            print(f"{'query':40} {'theme':18} {'all fields':>12} {'CS only':>10}")
            for query_key, theme, all_fields, cs in probe.probe_summary(conn, probed_at):
                print(f"{query_key:40} {theme:18} {all_fields or 0:>12,} {cs or 0:>10,}")
            print(f"probe cost ${cost:.4f}; rows in meta.recall_probes at probed_at={probed_at.isoformat()}")
            return 0

        if args.command == "ingest":
            client = _client(settings, http)
            cache = RawCache(settings.raw_data_dir)
            previous_run_id = conn.execute("SELECT max(run_id) FROM meta.ingestion_runs").fetchone()[0]
            try:
                if args.resume is not None:
                    result = pipeline.resume(conn, client, cache, args.resume)
                else:
                    result = pipeline.ingest(
                        conn, client, cache, load_recall_config(args.recall), pipeline.PROFILES[args.profile],
                        to_date=datetime.now(UTC).date(),
                    )
            except (OpenAlexError, psycopg.Error) as exc:
                # pipeline._execute marks a run 'failed' once it has a row to update -- but for a
                # fresh ingest that row only exists once runs.start_run's INSERT has committed. A
                # psycopg.Error raised before or during that INSERT (e.g. the connection drops)
                # leaves no row for this attempt, so there is nothing to resume. --resume's run
                # always already exists (it's why we're resuming it), so that id is unchanged.
                if args.resume is not None:
                    run_id = args.resume
                else:
                    latest_run_id = conn.execute("SELECT max(run_id) FROM meta.ingestion_runs").fetchone()[0]
                    run_id = latest_run_id if latest_run_id is not None and latest_run_id != previous_run_id else None
                if run_id is None:
                    print(f"ingest failed before a run was ever recorded, so it never started and cannot be "
                          f"resumed: {exc}", file=sys.stderr)
                else:
                    print(f"ingest run {run_id} failed: {exc}; resume with --resume {run_id}", file=sys.stderr)
                return 3
            print(f"run {result.run_id}: {result.status}, {result.works_fetched} works, ${result.cost_usd:.4f}")
            if result.message:
                print(result.message)
            return 0 if result.status == "succeeded" else 2

        if args.command == "ror":
            try:
                result = external_pipeline.enrich_ror(
                    conn,
                    _plain_http(http),
                    dump_dir=settings.raw_data_dir / "ror",
                    dump_path=args.dump,
                    match_affiliations=not args.no_match,
                    min_score=args.min_score,
                    max_affiliations=args.max_affiliations,
                )
            except (RorDumpError, httpx.HTTPError, psycopg.Error) as exc:
                print(f"ROR enrichment failed: {exc}", file=sys.stderr)
                return 3
            print(
                f"run {result.run_id}: ROR {result.version}, {result.organizations_loaded} organisations, "
                f"{result.institutions_linked} institutions linked, "
                f"{result.affiliations_matched} affiliation strings matched "
                f"({result.affiliations_unmatched} unmatched)"
            )
            return 0

        if args.command == "worldbank":
            to_year = args.to_year or datetime.now(UTC).year
            try:
                result = external_pipeline.enrich_worldbank(
                    conn, _plain_http(http), from_year=args.from_year, to_year=to_year
                )
            except (WorldBankError, httpx.HTTPError, psycopg.Error) as exc:
                print(f"World Bank enrichment failed: {exc}", file=sys.stderr)
                return 3
            print(
                f"run {result.run_id}: {result.countries} countries, {result.observations} observations "
                f"({result.missing_values} without a value), {args.from_year}-{to_year}"
            )
            return 0

        if args.command == "quality":
            results = run_quality_checks(conn, args.run)
            print(f"{'check':32} {'failing':>9} {'total':>9} {'ratio':>7}  gate")
            for r in results:
                gate = "metric" if r.passed is None else ("PASS" if r.passed else "FAIL")
                print(f"{r.name:32} {r.failing_rows:>9,} {r.total_rows:>9,} {r.ratio:>7.1%}  {gate}")
            return 0 if all(r.passed is not False for r in results) else 1

    return 1
