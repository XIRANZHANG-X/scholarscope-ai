"""Command-line entry point: `uv run scholarscope <command>`."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx

from scholarscope import db
from scholarscope.config import Settings
from scholarscope.ingestion import pipeline, probe
from scholarscope.ingestion.openalex_client import OpenAlexClient
from scholarscope.ingestion.raw_cache import RawCache
from scholarscope.ingestion.recall import load_recall_config
from scholarscope.quality.checks import run_quality_checks

DEFAULT_RECALL = db.PROJECT_ROOT / "config" / "recall.toml"
USER_AGENT = "ScholarScopeAI/0.1"


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
            if args.resume is not None:
                result = pipeline.resume(conn, client, cache, args.resume)
            else:
                result = pipeline.ingest(
                    conn, client, cache, load_recall_config(args.recall), pipeline.PROFILES[args.profile],
                    to_date=datetime.now(UTC).date(),
                )
            print(f"run {result.run_id}: {result.status}, {result.works_fetched} works, ${result.cost_usd:.4f}")
            if result.message:
                print(result.message)
            return 0 if result.status == "succeeded" else 2

        if args.command == "quality":
            results = run_quality_checks(conn, args.run)
            print(f"{'check':32} {'failing':>9} {'total':>9} {'ratio':>7}  gate")
            for r in results:
                gate = "metric" if r.passed is None else ("PASS" if r.passed else "FAIL")
                print(f"{r.name:32} {r.failing_rows:>9,} {r.total_rows:>9,} {r.ratio:>7.1%}  {gate}")
            return 0 if all(r.passed is not False for r in results) else 1

    return 1
