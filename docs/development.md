# Development quickstart

Prerequisites: Docker Desktop running ("Engine running"), [uv](https://docs.astral.sh/uv/), Git.

```bash
uv sync                         # locked environment + the scholarscope package (editable)
cp .env.example .env            # then set the passwords, and OPENALEX_API_KEY if you have one
docker compose up -d --wait     # PostgreSQL 17 + pgvector on 127.0.0.1:5432
uv run scholarscope migrate     # schema to the latest revision
uv run pytest                   # full suite (needs the database)
uv run pytest -m "not db"       # unit tests only
```

## Pipeline commands

| Command | What it does | OpenAlex cost (2026-09-22) |
|---|---|---|
| `uv run scholarscope probe` | Size of the RAG corpus by year and by type → `meta.recall_probes` | ≈ $0.0004 |
| `uv run scholarscope probe --recall config/context.toml` | Same for the FM / LLM / Agents context phrases (counts only, never downloaded) | ≈ $0.003 |
| `uv run scholarscope ingest --profile smoke` | 1 000 most relevant RAG works | ≈ $0.01 |
| `uv run scholarscope ingest --profile demo` | 5 000 most relevant RAG works | ≈ $0.05 |
| `uv run scholarscope ingest --profile standard` | The whole RAG corpus (15,921 works on 2026-09-22), paged by publication date | ≈ $0.16 |
| `uv run scholarscope ingest --resume RUN_ID` | Continue a `partial` or `failed` run from its checkpoints | — |
| `uv run scholarscope ror` | Download the ROR dump, load the corpus's organisations, match loose affiliations | free |
| `uv run scholarscope worldbank` | Refresh World Bank country profiles and indicator series | free |
| `uv run scholarscope quality --run RUN_ID` | Quality gates and metrics → `meta.data_quality_checks` | none |

The downloaded corpus is defined in `config/recall.toml`; `config/context.toml` holds context phrases
that are only probed. A run stores the exact queries, date window and limits it
started with, so `--resume` continues with identical filters even if the file changed since.

Budget: a keyword-search page (100 works) costs $0.001; a filter-only page or a `group_by` call costs
$0.0001. Keyless use gets $0.10/day, a free key $1/day. A run that reaches the budget ends `partial`;
resume it the next day. The key is sent as an `Authorization: Bearer` header, never in URLs.

Exit codes: `ingest` returns 0 when the run succeeded, 2 when it stopped `partial`, and 3 when it
hit an unexpected OpenAlex or database error (the run is recorded `failed`; resume it with
`--resume RUN_ID` once the underlying problem is fixed); `ror` and `worldbank` return 3 on an
unusable dump, an HTTP error or a database error, with the reason on stderr and the run recorded
`failed`; `quality` returns 1 when any gate fails.

## External data (Plan 2)

`ror` downloads the newest ROR release from Zenodo (~37 MB, CC0) into `data/raw/ror/`, loads only the
organisations the corpus references, and fills `external.institution_crosswalk`. It then asks ROR's
affiliation endpoint about institutions OpenAlex left without a ROR id and about raw affiliation
strings on authorships it never linked; each distinct string is asked once and cached in
`external.affiliation_matches`, including confident no-matches. Use `--dump PATH` to load a dump you
already have, `--no-match` to skip the API step, `--min-score` to change the 0.8 acceptance
threshold, and `--max-affiliations N` to bound how many strings one invocation asks about. That last
flag is for splitting a first, large run into sessions, not for surviving crashes: the matching loop
caches every 100 answers, so an interrupted run keeps what it already asked and the next run picks up
where it stopped.

A full `ror` run over the real corpus takes roughly 9 minutes, nearly all of it the one-per-string
affiliation calls; the dump download and load are a minute at most. Organisations a match pulls in
are refreshed by later releases too, so re-running against a newer dump updates the whole set.

`worldbank` refreshes `external.country_profiles` and six indicator series into
`external.country_indicators`. Coverage is uneven by design of the source: population is complete,
GDP nearly so, but R&D spending and researchers-per-million cover ~40% of countries and stop around
2023, and Taiwan is absent from the World Bank entirely. Values are stored as they come, NULLs
included; `scholarscope quality` reports the gaps.

Provenance: every external fact row carries the `run_id` that last wrote it, and both pipelines
commit each unit of work as they go, so an interrupted run keeps what it finished. A run that ends
`failed` can therefore own perfectly good rows — the corpus's crosswalk still holds 16 institution
matches written by a run whose process was killed afterwards. Join `meta.ingestion_runs` to learn
when a row arrived and from which release, but do not filter external facts by the run's `status`.

## Database

- Connect with host `127.0.0.1`, not `localhost` (Windows resolves `localhost` to `::1` first, which
  Docker Desktop leaves unanswered).
- `docker compose exec postgres psql -U scholarscope -d scholarscope` opens a SQL shell.
- `docker compose --profile graph up -d` also starts Neo4j (not needed until the graph milestone).

## Adding a migration

```bash
uv run alembic revision -m "short description" --rev-id 0003
```

Fill the generated file's `UPGRADE` and `DOWNGRADE` lists with raw SQL statements (one per list item),
then extend `tests/integration/test_migrations.py`.
