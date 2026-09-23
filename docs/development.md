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
| `uv run scholarscope quality --run RUN_ID` | Quality gates and metrics → `meta.data_quality_checks` | none |

The downloaded corpus is defined in `config/recall.toml`; `config/context.toml` holds context phrases
that are only probed. A run stores the exact queries, date window and limits it
started with, so `--resume` continues with identical filters even if the file changed since.

Budget: a keyword-search page (100 works) costs $0.001; a filter-only page or a `group_by` call costs
$0.0001. Keyless use gets $0.10/day, a free key $1/day. A run that reaches the budget ends `partial`;
resume it the next day. The key is sent as an `Authorization: Bearer` header, never in URLs.

Exit codes: `ingest` returns 0 when the run succeeded, 2 when it stopped `partial`, and 3 when it
hit an unexpected OpenAlex or database error (the run is recorded `failed`; resume it with
`--resume RUN_ID` once the underlying problem is fixed); `quality` returns 1 when any gate fails.

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
