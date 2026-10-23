# ScholarScope · RAG research, 2019–2026

**English** · [简体中文](README.zh-CN.md)

A reproducible bibliometric study of **retrieval-augmented generation (RAG)** research. It
collects every computer-science paper on OpenAlex whose title or abstract mentions
"retrieval augmented generation" (15,921 papers, 1 Jan 2019 – 22 Sep 2026), enriches it with
ROR institutions and World Bank country indicators, loads everything into PostgreSQL, and
answers five questions with saved SQL queries:

| # | Question | Main finding |
|---|---|---|
| 1 | How fast did RAG grow, compared with LLM, AI agents and foundation models? | RAG went from 133 papers (2023) to 1,966 (2024), 14.8× in one year, the steepest single-year rise of the four themes. |
| 2 | Where is it published, and how is each type cited? | About half of RAG papers are preprints (52% in 2024); preprints are cited least (55% of 2024 preprints cited at least once vs 88% of conference papers). |
| 3 | Which countries write it, and who works with whom? | China (2,494) and the US (2,306) appear on half of the papers with a known country; China–US is the largest partnership (301 joint papers). |
| 4 | What is it about, and what is gaining ground? | Agents rose from 11% to 24% of RAG papers (2024 → 2026); fine-tuning fell from 21% to 13%. |
| 5 | Does country wealth or R&D spending predict output? | Papers per person scale about 1:1 with GDP per capita (negative binomial elasticity 1.07); R&D share of GDP adds nothing measurable once wealth is controlled. |

Every number in the figures and on the website comes from a SQL query against the database;
The repository holds the queries and the code, not the data: query results (CSV) are written
locally when you run the scripts and are not committed.

**Outputs**

- 35 figures for slides (`analysis/*/output/`, `country_indicators_analysis/output/`): PNG at
  300 dpi (4000 × 2250) plus PDF. Running the scripts also saves each query's result as a CSV
  next to its figure (local only).
- A single-file website (`site/dist/scholarscope.html`, ~6 MB) with interactive versions of all
  35 charts. It opens offline with a double-click: no server, no internet connection.
  **Just want to look?** Download the prebuilt `scholarscope.html` from the [release](https://github.com/XIRANZHANG-X/scholarscope-ai/releases/tag/data-2026-09-22).

---

## Reproduce it

**Requirements:** [Docker Desktop](https://www.docker.com/products/docker-desktop/),
[uv](https://docs.astral.sh/uv/) (it installs Python 3.13 for you), about 2 GB of disk.

```bash
git clone https://github.com/XIRANZHANG-X/scholarscope-ai.git
cd scholarscope-ai
cp .env.example .env               # set POSTGRES_PASSWORD
uv sync
docker compose up -d --wait        # PostgreSQL 17 on 127.0.0.1:5432
```

Use `127.0.0.1`, not `localhost`: on Windows `localhost` resolves to `::1` first and the
connection stalls.

### Step 1 · Collect the data (about 20 minutes)

Put a free OpenAlex API key (<https://openalex.org/settings/api>) in `.env`. The full ingest costs
about US$0.16 of the $1/day free budget.

```bash
uv run scholarscope migrate
uv run scholarscope ingest --profile standard
uv run scholarscope ror
uv run scholarscope worldbank
uv run scholarscope quality
```

This pulls the corpus as it stands on OpenAlex today. OpenAlex keeps adding and updating
records, so your counts will differ slightly from the figures committed here (collected on
22 Sep 2026). Command details, exit codes and costs: [`docs/development.md`](docs/development.md).

Then add Taiwan once (it is not in the World Bank data; re-running is harmless):

```bash
docker compose exec -T postgres psql -U scholarscope -d scholarscope < country_indicators_analysis/sql/00_add_taiwan.sql
```

### Step 2 · Queries → figures

```bash
uv run python analysis/run_all.py --strict
```

Runs the five analysis scripts. Each executes its `sql/*.sql` over a read-only connection, saves
the result to `output/*.csv` and draws `output/*.png` / `*.pdf`. `--strict` fails instead of
silently falling back to the CSVs when the database is unreachable. After one database run,
`--from-csv` redraws from those local CSVs without connecting (for restyling only).

### Step 3 · Website

```bash
uv run python site/build_site.py
```

Runs the same queries and writes `site/dist/scholarscope.html` with the data and plotly.js inlined.
The page is a snapshot of the query results; rebuild it after the data changes.

### Check

```bash
uv run pytest
```

---

## Repository layout

```
src/scholarscope/        data collection: OpenAlex ingest, ROR and World Bank enrichment, quality checks (CLI: `scholarscope`)
sql/migrations/          database schema (Alembic): meta, core, bridge, external
compose.yaml             PostgreSQL 17 (+ optional pgAdmin: `docker compose --profile ui up -d`)
analysis/
  common.py              shared figure style and read-only query helper
  run_all.py             regenerate all five directions
  01_theme_comparison/   RAG vs LLM, agents and foundation models
  02_publication_types/  preprints, conference papers, articles; citations; venues
  03_world_collaboration/ countries, international co-authorship, maps
  04_subfields/          keyword-rule sub-directions of RAG (subfield_rules.csv)
country_indicators_analysis/  country wealth, R&D and population vs output (negative binomial models)
site/                    single-file website: build_site.py, charts.py, template, tokens
scripts/export_csv.py    export the database as CSV
docs/                    data dictionary, development notes, pipeline
tests/                   unit and integration tests
```

Each analysis folder has the same shape: `sql/` (one query per figure), `output/` (the figures;
the query results as CSV after a local run), `make_figures.py` (one function per figure) and a `README.md`
explaining every figure, its columns and its caveats.

**Language note:** the analysis READMEs, the figure style guide
([`analysis/图表统一规范.md`](analysis/图表统一规范.md)), the data dictionary
([`docs/数据字典.md`](docs/数据字典.md)) and the pipeline notes ([`docs/流程.md`](docs/流程.md))
are in Chinese. The country-indicator report
([`country_indicators_analysis/README.md`](country_indicators_analysis/README.md)) and
[`docs/development.md`](docs/development.md) are in English. All figures and the website are in English.

---

## Data caveats

Read these before reusing any number:

1. **15.2% of papers have a placeholder date** (1 January): OpenAlex knows only the year. Quarterly counts leave them out.
2. **41.4% of papers have no author country**, mostly preprints without affiliation text. Country results describe the rest.
3. **A paper counts once for every country on it** (full counting), so country totals exceed the paper count.
4. **2026 runs to 22 September**, and conference papers of the year are still being indexed. Figures mark it as 2026*.
5. **The LLM, agents and foundation-model counts are search-hit counts** (several phrases added together, so upper bounds); only the RAG papers themselves are in the database.
6. **Sub-directions are keyword rules**: a match means a topic is mentioned, not that it is the paper's subject. The title-only count is the stricter reading.
7. **Publication type is OpenAlex's label** and is noisy (for example, Zenodo uploads tagged as articles).

## Data sources and licences

| Source | Used for | Licence |
|---|---|---|
| [OpenAlex](https://openalex.org) | papers, authors, institutions, citations | CC0 |
| [ROR](https://ror.org) | institution names, countries, coordinates | CC0 |
| [World Bank Open Data](https://data.worldbank.org) | GDP per capita, R&D share, population | CC BY 4.0 |
| [Natural Earth](https://www.naturalearthdata.com) | map outlines | public domain |
| Taiwan's indicators (DGBAS, Ministry of the Interior, NSTC) | added by hand, see `country_indicators_analysis/README.md` | government open data |

The code is released under the [MIT licence](LICENSE). The data keep their original licences above.
