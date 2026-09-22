# ScholarScope AI 数据基座（Plan 1 / 8）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 Git 仓库、Docker Compose PostgreSQL、版本化 schema（`meta` / `core` / `bridge`），以及一条可断点续传、按 API 预算自动停止的 OpenAlex 采集流水线，并用真实 API 把 RAG 研究的 smoke 语料（1,000 篇）导入数据库、通过质量门。

**Architecture:** 互联网数据只经 `scholarscope.ingestion` 进入系统：召回查询配置 → OpenAlex 客户端（游标分页、按 `Retry-After` 重试、记录每次调用费用）→ 原始页 gzip 缓存 → 纯函数转换 → 幂等 upsert 进 PostgreSQL。每页一个事务，同一事务内推进断点，所以任何中断都能用 `--resume` 从最后一页继续。数据质量检查和规模探查都用 SQL 写回 `meta` schema。PostgreSQL 是唯一事实源（架构 §6）。

**Tech Stack:** Python 3.13（uv）、PostgreSQL 17.11 + pgvector 0.8.6（Docker Compose）、psycopg 3.3.6、SQLAlchemy 2.0.54 + Alembic 1.20.0（仅用于迁移）、httpx 0.28.1、tenacity 9.1.4、pydantic-settings 2.15.0、pytest 9.1.1。

## Global Constraints

- Python `>=3.13,<3.14`，一律用 `uv run ...` 执行；**不新增任何依赖**，本计划用到的库全部已在 `uv.lock` 中。
- 镜像固定为 `pgvector/pgvector:0.8.6-pg17-trixie` 与 `neo4j:2026.08.1-community-trixie`（2026-09-22 实测）。
- 数据库主机一律写 `127.0.0.1`，不写 `localhost`：本机上 `localhost` 先解析到 `::1`，Docker Desktop 对它不应答，实测每次连接卡满 `connect_timeout`（3 s），不设超时则永久挂起。
- 互联网数据只能经 ETL（`scholarscope.ingestion`）进入；分析、ML、界面和 Agent 只读 PostgreSQL（架构 §6）。
- 密钥只放在 `.env`（git 忽略）。OpenAlex Key 通过 `Authorization: Bearer` 头发送，不得出现在 URL、日志、原始缓存或提交中。
- 每篇入库论文都必须有纳入原因（`meta.work_recall_hits`，架构 §5.2），由质量门强制检查。
- 所有迁移是 Alembic 版本文件里的原生 SQL，版本表为 `meta.schema_versions`（架构 §7.1）。
- 采集时间窗：`from_publication_date:2019-01-01` 至运行当天（UTC），运行参数中保存截止日期（架构 §5.1）。
- 研究范围（2026-09-22 决定，方案 A）：下载的语料只有一条召回查询——计算机领域（primary topic field 17）中标题或摘要含 “retrieval augmented generation” 的论文；Foundation Models / LLM / Agents 只做聚合计数（`config/context.toml`），不下载明细。
- 开发机为 Windows 11；命令按 Git Bash 书写（PowerShell 5.1 会吞掉原生程序参数中的双引号）。
- AI Agent 创建的提交以 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` 结尾（下方提交命令已包含）。

## 本计划依据的实测事实（2026-09-22）

以下事实均在编写本计划时对真实 API 和本机 Docker 实测，计划中的代码已在临时副本里完整跑通：56 项测试全部通过，真实 API 的 probe 和 smoke 采集也成功。

**OpenAlex 计费**（响应体 `meta.cost_usd` 与 `X-RateLimit-*` 响应头）：

| 请求 | 单次费用 |
|---|---:|
| 纯筛选列表（如 `filter=type:article`） | $0.0001 |
| `group_by` 聚合（含检索条件时也一样） | $0.0001 |
| 含 `title_and_abstract.search` 的列表 | $0.001 |

无 Key 每天 $0.10，免费 Key 每天 $1。关键词检索每 100 篇 $0.001，所以完整 RAG 语料（约 1.6 万篇）约 $0.16：有 Key 一次跑完，无 Key 要分两天（第二天 `--resume`）。架构 §4.1 原先按纯筛选价格估算，已在 2026-09-22 更正。

**数据怪癖**（代码必须处理）：

1. 顶层 `institutions` 可能为空，而 `authorships[].institutions` 有值 → 机构一律从 authorships 取。
2. `authorships[].author.id` 可能为 `null`（作者未消歧）→ 保留该作者位，`author_id` 置空。
3. 摘要以倒排索引给出，常以 `Abstract` 标签词开头 → 重建时去掉。
4. OpenAlex 新增独立类型 `conference-paper`；只筛 `article|preprint|review` 会丢掉 NeurIPS/ACL 类论文。
5. OpenAlex topic 有明显噪声（例：一篇幻觉综述被标为 “Ferroelectric and Negative Capacitance Devices”，score 0.98）→ 印证架构 §5.2 的两阶段筛选。
6. 预印本经常没有机构和参考文献。
7. 相关度分数在两次请求之间会轻微漂移（同一篇论文 73.71 → 73.64），按相关度翻页时页边界的论文会重复或遗漏：1,000 篇中重复 5 篇。按发表日期排序时游标用论文 ID 打破平局，1,000 篇无一重复 → 完整语料（standard 档）按日期翻页，smoke/demo 子集仍按相关度取“最相关的 N 篇”。

**范围决定（方案 A）**：只下载 RAG 语料，共 15,921 篇（仅 CS）。年度分布：

| 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026（至 9/22） |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 5 | 14 | 19 | 133 | 1,966 | 5,505 | 8,277 |

背景短语（`config/context.toml`，只做计数，不下载；类型 article/preprint/conference-paper/review）：

| 查询 | 全领域 | 仅 CS（field 17） |
|---|---:|---:|
| `llm.large_language_model` “large language model” | 248,626 | 146,938 |
| `llm.chatgpt` “ChatGPT” | 71,306 | 18,616 |
| `fm.foundation_model` “foundation model” | 35,438 | 15,988 |
| `rag.retrieval_augmented_generation` | 24,042 | 15,921 |
| `agents.ai_agent` “AI agent” | 20,572 | 9,879 |
| `agents.agentic_ai` “agentic AI” | 13,058 | 5,459 |
| `agents.llm_agent` “LLM agent” | 11,092 | 7,257 |
| `fm.pretrained_language_model` | 3,975 | 3,261 |

决定理由：单个 “large language model” 短语就有 24.9 万篇（CS 14.7 万），多为把大模型当工具的应用论文。RAG 起点清晰（2020 年提出，2023 年后爆发），规模可控。RAG 与 Agent 的交叉可以直接支撑“研究机会”叙事：同时出现 agent 一词的 RAG 论文，2024 年 207 篇、2025 年 925 篇、2026 年 1,934 篇。

**真实 smoke 运行（RAG，按相关度）**：1,000 次命中、995 篇去重（重复原因见第 7 条），耗时 25 s，花费 $0.010，质量门全部通过。各项指标：

| 指标 | 数值 |
|---|---:|
| 摘要缺失 | 13.4% |
| 无机构论文 | 25.8% |
| 作者未消歧 | 7.2% |
| 机构缺 ROR | 0.7% |
| 参考文献落在语料外 | 93.0% |

## 范围

**本计划包含：** 仓库初始化、Compose、`meta`/`core`/`bridge` 三个 schema、OpenAlex 采集（RAG 语料的 probe / smoke / demo / standard 档、背景短语探查、断点续传、预算保护）、质量检查、CLI、开发文档。

**不包含（后续各自一份计划）：**

2. 外部数据融合：ROR 机构标准化（含架构 §7.3 的 `bridge.institution_relationships`）、World Bank 指标，并决定是否接入 Crossref（`external` schema）
3. 分析层：物化视图、窗口函数指标、`EXPLAIN ANALYZE` 对比（`analytics` schema）
4. 机器学习：人工标注集、多标签分类、向量与混合检索、趋势回测、影响力预测（`ml` schema）
5. 图：Neo4j 子图导出与图分析
6. Research Agent + Skills + 评测（`audit` schema）
7. Streamlit + Hallmark 界面
8. 交付：双语 README、CI、Release、PPT

## 执行前置条件

- Docker Desktop 已启动（左下角 Engine running）；端口 5432、7474、7687 空闲（2026-09-22 已确认）。
- 项目根目录 `D:\PyCharm\PyStudy\Database`，已有 `pyproject.toml`、`uv.lock`、`.python-version`、`.venv\`。
- OpenAlex Key 可选：Task 13 的必做步骤共约 $0.025，在无 Key 的每日 $0.10 以内；可选的完整语料下载（约 $0.16）需要 Key，或无 Key 分两天续传。
- 真实数据夹具已随本计划保存在 `docs/superpowers/plans/assets/2026-09-22-openalex-works-page.json`（3 篇真实论文，OpenAlex 数据为 CC0，可再分发）。

## 文件结构

```text
.gitignore / .gitattributes / .env.example     仓库规则；.env 只在本地
compose.yaml                                   PostgreSQL（默认）+ Neo4j（profile graph）
alembic.ini                                    Alembic 入口；URL 由 env.py 从 .env 生成
config/recall.toml                             下载语料的召回查询：RAG、仅 CS（数据，不是代码）
config/context.toml                            背景短语：FM / LLM / Agents，只做 probe 计数
sql/migrations/env.py                          Alembic 环境；版本表 meta.schema_versions
sql/migrations/script.py.mako                  新迁移模板（UPGRADE/DOWNGRADE 原生 SQL 列表）
sql/migrations/versions/0001_meta_schema.py    meta：来源、运行、断点、质量、探查
sql/migrations/versions/0002_core_bridge_schema.py  core + bridge + meta.work_recall_hits
src/scholarscope/config.py                     Settings：环境变量 / .env
src/scholarscope/db.py                         连接、SQLAlchemy URL、migrate/downgrade
src/scholarscope/ingestion/recall.py           召回配置与 OpenAlex filter 构造
src/scholarscope/ingestion/openalex_client.py  HTTP 客户端：游标、费用、重试、预算耗尽
src/scholarscope/ingestion/transform.py        一条 work JSON → 各表行（纯函数）
src/scholarscope/ingestion/runs.py             meta.ingestion_runs 与断点读写
src/scholarscope/ingestion/loader.py           幂等 upsert 进 core/bridge
src/scholarscope/ingestion/raw_cache.py        原始页 gzip 缓存
src/scholarscope/ingestion/pipeline.py         采集编排：档位、预算、续传
src/scholarscope/ingestion/probe.py            范围探查（group_by）写入 meta.recall_probes
src/scholarscope/quality/checks.py             SQL 质量门与指标
src/scholarscope/cli.py                        `scholarscope` 命令
tests/conftest.py                              夹具：settings、测试库、自动清表的连接
tests/support.py                               FakeOpenAlex 测试替身、两查询召回配置
tests/fixtures/openalex/works_page.json        3 篇真实论文（CC0）
tests/unit/…  tests/integration/…              单元测试 / 需要数据库的测试（marker `db`）
docs/development.md                            开发者快速上手
```

---

### Task 1: 仓库初始化与运行配置

**Files:**
- Create: `.gitignore`, `.gitattributes`, `.env.example`, `src/scholarscope/__init__.py`, `src/scholarscope/config.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/test_config.py`
- Modify: `pyproject.toml`（改为可安装包，加 pytest 配置）, `uv.lock`（`uv lock` 重新生成）

**Interfaces:**
- Consumes: 无
- Produces: `scholarscope.config.Settings`，字段 `postgres_host: str = "127.0.0.1"`、`postgres_port: int = 5432`、`postgres_user: str = "scholarscope"`、`postgres_password: SecretStr`（必填）、`postgres_db: str = "scholarscope"`、`openalex_base_url: str = "https://api.openalex.org"`、`openalex_api_key: SecretStr | None`（空串视为 `None`）、`raw_data_dir: Path = Path("data/raw")`。环境变量名即字段名（大小写不敏感），默认读取当前目录的 `.env`。

- [ ] **Step 1: 建仓库，先提交已有文件**

个人简历与答辩手册 `简历深扒.md` 只留在本地：写入 `.git/info/exclude`，不进入公开仓库。

```bash
git init
printf '%s\n' '简历深扒.md' >> .git/info/exclude
```

创建 `.gitignore`：

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
.pytest_cache/

# Local configuration and secrets (template: .env.example)
.env

# Downloaded API data; rebuild with `uv run scholarscope ingest`
data/raw/

# Editor and note-vault settings
.idea/
.obsidian/
```

创建 `.gitattributes`：

```gitattributes
# Normalise line endings in the repository; teammates on Windows and macOS check out native endings.
* text=auto
```

```bash
git add .gitignore .gitattributes .python-version pyproject.toml uv.lock 任务架构.md docs/superpowers
git commit -m "chore: initial commit with architecture blueprint and data-foundation plan" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git switch -c feat/data-foundation
git status --short
```

Expected：最后一条命令无输出（`简历深扒.md`、`.obsidian/`、`.venv/` 均被忽略）。

- [ ] **Step 2: 写失败测试**

创建 `tests/__init__.py` 和 `tests/unit/__init__.py`（均为空文件），再创建 `tests/unit/test_config.py`：

```python
from scholarscope.config import Settings


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_PORT", "55432")
    monkeypatch.setenv("OPENALEX_API_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.postgres_port == 55432
    assert settings.postgres_password.get_secret_value() == "s3cret"
    assert settings.openalex_api_key is None


def test_secrets_are_masked(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("OPENALEX_API_KEY", "key-123")
    settings = Settings(_env_file=None)
    assert settings.openalex_api_key.get_secret_value() == "key-123"
    assert "s3cret" not in repr(settings) and "key-123" not in repr(settings)
```

- [ ] **Step 3: 确认失败**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: 收集阶段报错 `ModuleNotFoundError: No module named 'scholarscope'`

- [ ] **Step 4: 把项目改为可安装包，并实现 Settings**

用下面内容**整体替换** `pyproject.toml`（删掉了 `[tool.uv] package = false`，新增 build-system、`[tool.uv.build-backend]` 和 pytest 配置，依赖不变）：

```toml
[project]
name = "scholarscope-ai"
version = "0.1.0"
description = "ScholarScope AI: multi-source research intelligence on Foundation Models, LLM, RAG and AI Agents"
requires-python = ">=3.13,<3.14"
dependencies = [
    # Database access: PostgreSQL + pgvector, Neo4j, migrations (section 7, 8)
    "psycopg[binary,pool]>=3.3",
    "sqlalchemy>=2.0",
    "alembic>=1.20",
    "pgvector>=0.5",
    "neo4j>=6.3",
    # ETL: OpenAlex / ROR / World Bank APIs with retry, validated config (section 4)
    "httpx>=0.28",
    "tenacity>=9.1",
    "pydantic>=2.13",
    "pydantic-settings>=2.10",
    # Analysis and ML: EDA, classification, impact prediction, explanations, graphs (section 9)
    "pandas>=3.0",
    "scikit-learn>=1.9",
    "lightgbm>=4.7",
    "shap>=0.52",
    "networkx>=3.7",
    # Embeddings and small Transformer classifiers (section 9.2); torch comes from the CUDA 13.0 index below
    "torch>=2.14",
    "sentence-transformers>=6.1",
    "transformers>=5.17",
    # LLM providers behind the LLMProvider interface; SQL parsing for the sql-analyst skill (section 10)
    "openai>=3.17",
    "ollama>=0.6",
    "sqlglot>=30.18",
    # Data product UI (section 11)
    "streamlit>=1.64",
    "plotly>=7.1",
]

[build-system]
requires = ["uv_build>=0.12.17,<0.13"]
build-backend = "uv_build"

[dependency-groups]
dev = [
    "pytest>=9.1",
]

# RTX 50-series (sm_120) needs CUDA 13.0 builds of PyTorch; macOS falls back to the default PyPI wheel.
[[tool.uv.index]]
name = "pytorch-cu130"
url = "https://download.pytorch.org/whl/cu130"
explicit = true

[tool.uv.sources]
torch = [{ index = "pytorch-cu130", marker = "sys_platform == 'win32' or sys_platform == 'linux'" }]

[tool.uv.build-backend]
module-name = "scholarscope"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
markers = ["db: needs the PostgreSQL service from compose.yaml (docker compose up -d)"]
```

创建 `src/scholarscope/__init__.py`：

```python
"""ScholarScope AI: research intelligence on Foundation Models, LLM, RAG and AI Agents."""
```

创建 `src/scholarscope/config.py`：

```python
"""Runtime configuration, read from environment variables and the project `.env` file."""

from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Connection and API settings.

    Field names double as environment variable names (case-insensitive), so the
    same `.env` feeds both Docker Compose (`POSTGRES_*`) and the Python code.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Not "localhost": on Windows it resolves to ::1 first, which Docker Desktop leaves unanswered
    # (compose binds 127.0.0.1 only), so every connection would stall until connect_timeout.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "scholarscope"
    postgres_password: SecretStr
    postgres_db: str = "scholarscope"

    openalex_base_url: str = "https://api.openalex.org"
    openalex_api_key: SecretStr | None = None

    raw_data_dir: Path = Path("data/raw")

    @field_validator("openalex_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, value: object) -> object:
        return value or None
```

创建 `.env.example`：

```dotenv
# Copy to .env and fill in. .env is git-ignored: never commit real secrets.

# PostgreSQL — read by docker compose AND by scholarscope.config.Settings.
POSTGRES_USER=scholarscope
POSTGRES_PASSWORD=change-me
POSTGRES_DB=scholarscope
# Keep 127.0.0.1: "localhost" resolves to ::1 first on Windows and stalls every connection.
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432

# Neo4j — only used by `docker compose --profile graph up -d`. At least 8 characters.
NEO4J_PASSWORD=change-me-neo4j

# OpenAlex key from https://openalex.org/settings/api (free, $1/day budget).
# Leave empty to run keyless ($0.10/day: enough for `probe` and the smoke profile).
OPENALEX_API_KEY=

# Where raw API pages are cached (git-ignored).
RAW_DATA_DIR=data/raw
```

- [ ] **Step 5: 重新锁定并安装**

```bash
uv lock
uv sync
git diff --stat uv.lock
```

Expected：`uv.lock | 2 +-`，唯一变化是本项目条目 `source = { virtual = "." }` 变为 `source = { editable = "." }`，所有第三方包版本不变。

- [ ] **Step 6: 确认通过**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: `2 passed`

- [ ] **Step 7: 提交**

```bash
git add .env.example pyproject.toml uv.lock src tests
git commit -m "feat: installable scholarscope package with environment settings" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Docker Compose PostgreSQL、Alembic 与 meta schema

**Files:**
- Create: `compose.yaml`, `alembic.ini`, `sql/migrations/env.py`, `sql/migrations/script.py.mako`, `sql/migrations/versions/0001_meta_schema.py`, `src/scholarscope/db.py`, `tests/conftest.py`, `tests/integration/__init__.py`, `tests/unit/test_db.py`, `tests/integration/test_database_server.py`, `tests/integration/test_migrations.py`
- Create（本地，不提交）: `.env`

**Interfaces:**
- Consumes: `Settings`（Task 1）
- Produces:
  - `scholarscope.db.PROJECT_ROOT: Path`，`CONNECT_TIMEOUT_S = 5`
  - `connect(settings: Settings, dbname: str | None = None, *, autocommit: bool = False) -> psycopg.Connection`
  - `sqlalchemy_url(settings: Settings, dbname: str | None = None) -> sqlalchemy.engine.URL`
  - `migrate(settings, dbname=None, revision="head") -> None`；`downgrade(settings, dbname=None, revision="base") -> None`
  - pytest 夹具（`tests/conftest.py`）：`works_page`（每次返回新副本；文件在 Task 6 放入）、`settings`（session）、`test_db`（session，库名 `scholarscope_test`，已迁移到 head）、`db`（autocommit 连接，测试后清空除 `meta.schema_versions`、`meta.data_sources` 外的所有表）。需要数据库的测试模块写 `pytestmark = pytest.mark.db`。
  - 表：`meta.data_sources`（预置 `openalex` 行，license `CC0 1.0`）、`meta.ingestion_runs`、`meta.ingestion_checkpoints`、`meta.data_quality_checks`、`meta.recall_probes`；版本表 `meta.schema_versions`。

- [ ] **Step 1: 生成本地 `.env`（随机密码，不提交）**

```bash
test -f .env || uv run python -c "import pathlib, secrets; t = pathlib.Path('.env.example').read_text(encoding='utf-8'); t = t.replace('POSTGRES_PASSWORD=change-me', 'POSTGRES_PASSWORD=' + secrets.token_urlsafe(18), 1).replace('NEO4J_PASSWORD=change-me-neo4j', 'NEO4J_PASSWORD=' + secrets.token_urlsafe(18), 1); pathlib.Path('.env').write_text(t, encoding='utf-8')"
git status --short
```

Expected：`git status` 不显示 `.env`。注意：PostgreSQL 只在数据卷首次初始化时读取密码；以后改 `.env` 里的密码不会改变已有卷里的密码。

- [ ] **Step 2: 写 Compose 并启动 PostgreSQL**

创建 `compose.yaml`：

```yaml
# ScholarScope AI local services (architecture §12).
#   docker compose up -d                   -> PostgreSQL only (all ETL and analysis work)
#   docker compose --profile graph up -d   -> PostgreSQL + Neo4j (graph milestone)
# Images are pinned to the tags verified on 2026-09-22; offline backups live in D:\Docker\images\.

name: scholarscope

services:
  postgres:
    image: pgvector/pgvector:0.8.6-pg17-trixie
    environment:
      POSTGRES_USER: ${POSTGRES_USER:?set POSTGRES_USER in .env}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: ${POSTGRES_DB:?set POSTGRES_DB in .env}
    command:
      - postgres
      - -c
      - shared_buffers=2GB
      - -c
      - maintenance_work_mem=1GB
      - -c
      - work_mem=64MB
      - -c
      - max_wal_size=4GB
    # Parallel HNSW index builds need more than Docker's default 64 MB of shared memory.
    shm_size: 1gb
    ports:
      - "127.0.0.1:${POSTGRES_PORT:-5432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      # -h forces TCP, so the check stays red while initdb's socket-only bootstrap server is running.
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 30

  neo4j:
    image: neo4j:2026.08.1-community-trixie
    profiles: ["graph"]
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:?set NEO4J_PASSWORD in .env}
      NEO4J_server_memory_heap_initial__size: 2G
      NEO4J_server_memory_heap_max__size: 2G
      NEO4J_server_memory_pagecache_size: 2G
    ports:
      - "127.0.0.1:7474:7474"
      - "127.0.0.1:7687:7687"
    volumes:
      - neo4jdata:/data
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://localhost:7474 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 30

volumes:
  pgdata:
  neo4jdata:
```

```bash
docker compose config --quiet
docker compose up -d --wait
```

Expected：第一条无输出（配置有效）；第二条最后一行为 `Container scholarscope-postgres-1 Healthy`。

- [ ] **Step 3: 写失败测试**

创建 `tests/integration/__init__.py`（空文件）。

创建 `tests/conftest.py`：

```python
import json
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from scholarscope.config import Settings
from scholarscope.db import connect, migrate

FIXTURES = Path(__file__).parent / "fixtures"
TEST_DB = "scholarscope_test"
# Reference data that migrations seed; every other table is emptied after each test.
KEEP_TABLES = {"meta.schema_versions", "meta.data_sources"}


@pytest.fixture
def works_page() -> dict:
    """Three real OpenAlex works (CC0), trimmed; captured 2026-09-22. Fresh copy per test."""
    return json.loads((FIXTURES / "openalex" / "works_page.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def test_db(settings: Settings) -> str:
    """A freshly migrated database used by all `db` tests in the session."""
    try:
        admin = connect(settings, "postgres", autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.fail(
            f"PostgreSQL is not reachable ({exc}). Start it with `docker compose up -d`, "
            'or run only unit tests with `uv run pytest -m "not db"`.',
            pytrace=False,
        )
    with admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    migrate(settings, TEST_DB)
    return TEST_DB


@pytest.fixture
def db(settings: Settings, test_db: str):
    """Autocommit connection to the test database; all data is truncated afterwards."""
    with connect(settings, test_db, autocommit=True) as conn:
        yield conn
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT schemaname || '.' || tablename FROM pg_tables "
                "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
            ).fetchall()
            if name not in KEEP_TABLES
        ]
        conn.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
```

创建 `tests/unit/test_db.py`：

```python
from scholarscope.config import Settings
from scholarscope.db import sqlalchemy_url


def test_sqlalchemy_url_escapes_password(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:w/rd")
    url = sqlalchemy_url(Settings(_env_file=None), "other_db")
    assert url.database == "other_db"
    assert url.password == "p@ss:w/rd"
    assert "p%40ss%3Aw%2Frd@" in url.render_as_string(hide_password=False)
    assert url.query["connect_timeout"] == "5"
```

创建 `tests/integration/test_database_server.py`：

```python
import pytest

pytestmark = pytest.mark.db


def test_server_matches_compose_configuration(db):
    """Guards compose.yaml: PostgreSQL 17, pgvector 0.8.6 available, memory settings from architecture §12.3."""
    assert db.execute("SHOW server_version_num").fetchone()[0].startswith("17")
    assert db.execute("SELECT default_version FROM pg_available_extensions WHERE name = 'vector'").fetchone() == (
        "0.8.6",
    )
    assert db.execute("SHOW shared_buffers").fetchone() == ("2GB",)
    assert db.execute("SHOW maintenance_work_mem").fetchone() == ("1GB",)
```

创建 `tests/integration/test_migrations.py`（本任务只覆盖迁移 0001；Task 3 会整体替换）：

```python
import pytest
from psycopg import sql

from scholarscope.db import connect, downgrade, migrate

pytestmark = pytest.mark.db

MIGRATION_DB = "scholarscope_migration_test"
EXPECTED_RELATIONS = {
    "meta.schema_versions", "meta.data_sources", "meta.ingestion_runs", "meta.ingestion_checkpoints",
    "meta.data_quality_checks", "meta.recall_probes",
}


@pytest.fixture
def scratch_db(settings):
    drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(MIGRATION_DB))
    with connect(settings, "postgres", autocommit=True) as admin:
        admin.execute(drop)
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(MIGRATION_DB)))
    yield MIGRATION_DB
    with connect(settings, "postgres", autocommit=True) as admin:
        admin.execute(drop)


def relations(settings, dbname) -> set[str]:
    with connect(settings, dbname) as conn:
        rows = conn.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables "
            "WHERE table_schema IN ('meta', 'core', 'bridge')"
        ).fetchall()
    return {r[0] for r in rows}


def test_upgrade_creates_every_table_and_view(settings, scratch_db):
    migrate(settings, scratch_db)
    assert relations(settings, scratch_db) == EXPECTED_RELATIONS
    with connect(settings, scratch_db) as conn:
        assert conn.execute("SELECT version_num FROM meta.schema_versions").fetchone() == ("0001",)
        assert conn.execute("SELECT license FROM meta.data_sources WHERE source_id = 'openalex'").fetchone() == (
            "CC0 1.0",
        )


def test_downgrade_to_base_then_upgrade_again(settings, scratch_db):
    migrate(settings, scratch_db)
    downgrade(settings, scratch_db, "base")
    assert relations(settings, scratch_db) == {"meta.schema_versions"}
    migrate(settings, scratch_db)
    assert relations(settings, scratch_db) == EXPECTED_RELATIONS
```

- [ ] **Step 4: 确认失败**

Run: `uv run pytest -v`
Expected: `ImportError while loading conftest`，原因 `ModuleNotFoundError: No module named 'scholarscope.db'`

- [ ] **Step 5: 实现数据库模块与迁移框架**

创建 `src/scholarscope/db.py`：

```python
"""PostgreSQL connections and schema migrations."""

from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL

from scholarscope.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONNECT_TIMEOUT_S = 5


def connect(settings: Settings, dbname: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    """Open a psycopg connection to `dbname` (default: the configured project database)."""
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=dbname or settings.postgres_db,
        autocommit=autocommit,
        connect_timeout=CONNECT_TIMEOUT_S,
    )


def sqlalchemy_url(settings: Settings, dbname: str | None = None) -> URL:
    """SQLAlchemy URL for the same database; URL.create escapes special characters in the password."""
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=dbname or settings.postgres_db,
        query={"connect_timeout": str(CONNECT_TIMEOUT_S)},
    )


def alembic_config(url: URL) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "sql" / "migrations"))
    cfg.attributes["sqlalchemy_url"] = url
    return cfg


def migrate(settings: Settings, dbname: str | None = None, revision: str = "head") -> None:
    """Upgrade `dbname` to `revision`."""
    command.upgrade(alembic_config(sqlalchemy_url(settings, dbname)), revision)


def downgrade(settings: Settings, dbname: str | None = None, revision: str = "base") -> None:
    """Downgrade `dbname` to `revision`."""
    command.downgrade(alembic_config(sqlalchemy_url(settings, dbname)), revision)
```

创建 `alembic.ini`：

```ini
# Alembic configuration. Migrations are raw SQL in sql/migrations/versions/.
# The database URL is not stored here: env.py builds it from `.env` via scholarscope.config.Settings.

[alembic]
script_location = %(here)s/sql/migrations
path_separator = os
file_template = %%(rev)s_%%(slug)s

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

创建 `sql/migrations/env.py`：

```python
"""Alembic environment: online migrations only, version table kept in meta.schema_versions."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

url = config.attributes.get("sqlalchemy_url")
if url is None:
    from scholarscope.config import Settings
    from scholarscope.db import sqlalchemy_url

    url = sqlalchemy_url(Settings())

engine = create_engine(url, poolclass=pool.NullPool)
with engine.connect() as connection:
    # The version table lives in `meta`, so the schema must exist before Alembic looks for it.
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS meta"))
    connection.commit()
    context.configure(
        connection=connection,
        version_table="schema_versions",
        version_table_schema="meta",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()
```

创建 `sql/migrations/script.py.mako`：

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None

UPGRADE: list[str] = []

DOWNGRADE: list[str] = []


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
```

创建 `sql/migrations/versions/0001_meta_schema.py`：

```python
"""meta schema: data sources, ingestion runs and checkpoints, quality checks, recall probes

Revision ID: 0001
Revises:
Create Date: 2026-09-22
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = [
    # The `meta` schema itself is created by env.py because Alembic's version table lives in it.
    """
    CREATE TABLE meta.data_sources (
        source_id     text PRIMARY KEY,
        name          text NOT NULL,
        url           text NOT NULL,
        license       text NOT NULL,
        notes         text,
        registered_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    INSERT INTO meta.data_sources (source_id, name, url, license, notes) VALUES
        ('openalex', 'OpenAlex', 'https://openalex.org', 'CC0 1.0',
         'Works, authors, institutions, sources, topics, keywords and references via the REST API.')
    """,
    """
    CREATE TABLE meta.ingestion_runs (
        run_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source_id       text NOT NULL REFERENCES meta.data_sources,
        profile         text NOT NULL,
        params          jsonb NOT NULL,
        status          text NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
        started_at      timestamptz NOT NULL DEFAULT now(),
        finished_at     timestamptz,
        requests_made   integer NOT NULL DEFAULT 0,
        records_fetched integer NOT NULL DEFAULT 0,
        cost_usd        numeric(10, 4) NOT NULL DEFAULT 0,
        message         text
    )
    """,
    """
    CREATE TABLE meta.ingestion_checkpoints (
        run_id        bigint NOT NULL REFERENCES meta.ingestion_runs ON DELETE CASCADE,
        query_key     text NOT NULL,
        next_cursor   text,
        pages_done    integer NOT NULL DEFAULT 0,
        works_fetched integer NOT NULL DEFAULT 0,
        is_exhausted  boolean NOT NULL DEFAULT false,
        updated_at    timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, query_key)
    )
    """,
    """
    CREATE TABLE meta.data_quality_checks (
        check_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id       bigint REFERENCES meta.ingestion_runs ON DELETE CASCADE,
        check_name   text NOT NULL,
        description  text NOT NULL,
        failing_rows bigint NOT NULL,
        total_rows   bigint NOT NULL,
        threshold    numeric,
        passed       boolean,
        checked_at   timestamptz NOT NULL DEFAULT now()
    )
    """,
    "COMMENT ON COLUMN meta.data_quality_checks.threshold IS "
    "'Maximum allowed failing_rows/total_rows; NULL means the check is a tracked metric, not a gate'",
    """
    CREATE TABLE meta.recall_probes (
        probe_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        probed_at   timestamptz NOT NULL,
        query_key   text NOT NULL,
        theme       text NOT NULL,
        field_scope text NOT NULL CHECK (field_scope IN ('all', 'cs')),
        dimension   text NOT NULL CHECK (dimension IN ('publication_year', 'type')),
        bucket      text NOT NULL,
        works_count integer NOT NULL,
        filter      text NOT NULL
    )
    """,
]

DOWNGRADE = [
    "DROP TABLE meta.recall_probes",
    "DROP TABLE meta.data_quality_checks",
    "DROP TABLE meta.ingestion_checkpoints",
    "DROP TABLE meta.ingestion_runs",
    "DROP TABLE meta.data_sources",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
```

- [ ] **Step 6: 确认通过，并验证 Alembic 命令行**

Run: `uv run pytest -v`
Expected: `6 passed`

```bash
uv run alembic upgrade head
uv run alembic current
```

Expected：`alembic current` 最后一行为 `0001 (head)`（这是项目主库 `scholarscope`；测试用的是独立的 `scholarscope_test`）。

- [ ] **Step 7: 提交**

```bash
git add compose.yaml alembic.ini sql src/scholarscope/db.py tests
git status --short
git commit -m "feat: compose PostgreSQL with Alembic raw-SQL migrations and meta schema" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

Expected：提交前的 `git status --short` 里没有 `.env`。

---

### Task 3: core 与 bridge schema（迁移 0002）

**Files:**
- Create: `sql/migrations/versions/0002_core_bridge_schema.py`
- Modify: `tests/integration/test_migrations.py`（整体替换）

**Interfaces:**
- Consumes: 迁移 0001（Task 2）
- Produces（后续任务依赖的表和列）：
  - `core.countries(country_code char(2) PK, name)`、`core.sources(source_id PK, display_name, type, issn_l, host_organization_name)`、`core.topics(topic_id PK, display_name, subfield_id, subfield_name, field_id, field_name, domain_id, domain_name)`、`core.keywords(keyword_id PK, display_name)`、`core.institutions(institution_id PK, display_name, ror_id, country_code FK, type)`、`core.authors(author_id PK, display_name, orcid)`
  - `core.works(work_id PK CHECK '^W[0-9]+$', doi, title, abstract, publication_date, publication_year, type, language, source_id FK, is_oa, oa_status, cited_by_count, fwci, citation_percentile, is_top_1pct, is_top_10pct, referenced_works_count, is_retracted, openalex_updated_at, first_run_id FK, last_run_id FK, loaded_at)`
  - `core.work_yearly_citations(work_id, year, cited_by_count)`
  - `bridge.work_authors(work_id, author_seq, author_id NULL 可, raw_author_name, author_position, is_corresponding, raw_affiliation_strings text[])`、`bridge.authorship_institutions(work_id, author_seq, institution_id)`、`bridge.authorship_countries(work_id, author_seq, country_code)`、`bridge.work_topics(work_id, topic_id, score, is_primary)`、`bridge.work_keywords(work_id, keyword_id, score)`、`bridge.work_references(work_id, referenced_work_id)`（无外键：多数被引论文在语料外）
  - 视图 `bridge.work_institutions`、`bridge.author_affiliations(author_id, institution_id, first_year, last_year, works_count)`
  - `meta.work_recall_hits(work_id, query_key, first_run_id, last_run_id, relevance_score)`

- [ ] **Step 1: 更新迁移测试（先让它失败）**

用下面内容整体替换 `tests/integration/test_migrations.py`：

```python
import psycopg
import pytest
from psycopg import sql

from scholarscope.db import connect, downgrade, migrate

pytestmark = pytest.mark.db

MIGRATION_DB = "scholarscope_migration_test"
EXPECTED_RELATIONS = {
    "meta.schema_versions", "meta.data_sources", "meta.ingestion_runs", "meta.ingestion_checkpoints",
    "meta.data_quality_checks", "meta.recall_probes", "meta.work_recall_hits",
    "core.countries", "core.sources", "core.topics", "core.keywords", "core.institutions", "core.authors",
    "core.works", "core.work_yearly_citations",
    "bridge.work_authors", "bridge.authorship_institutions", "bridge.authorship_countries", "bridge.work_topics",
    "bridge.work_keywords", "bridge.work_references", "bridge.work_institutions", "bridge.author_affiliations",
}


@pytest.fixture
def scratch_db(settings):
    drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(MIGRATION_DB))
    with connect(settings, "postgres", autocommit=True) as admin:
        admin.execute(drop)
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(MIGRATION_DB)))
    yield MIGRATION_DB
    with connect(settings, "postgres", autocommit=True) as admin:
        admin.execute(drop)


def relations(settings, dbname) -> set[str]:
    with connect(settings, dbname) as conn:
        rows = conn.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables "
            "WHERE table_schema IN ('meta', 'core', 'bridge')"
        ).fetchall()
    return {r[0] for r in rows}


def test_upgrade_creates_every_table_and_view(settings, scratch_db):
    migrate(settings, scratch_db)
    assert relations(settings, scratch_db) == EXPECTED_RELATIONS
    with connect(settings, scratch_db) as conn:
        assert conn.execute("SELECT version_num FROM meta.schema_versions").fetchone() == ("0002",)
        assert conn.execute("SELECT license FROM meta.data_sources WHERE source_id = 'openalex'").fetchone() == (
            "CC0 1.0",
        )


def test_downgrade_to_base_then_upgrade_again(settings, scratch_db):
    migrate(settings, scratch_db)
    downgrade(settings, scratch_db, "base")
    assert relations(settings, scratch_db) == {"meta.schema_versions"}
    migrate(settings, scratch_db)
    assert relations(settings, scratch_db) == EXPECTED_RELATIONS


def test_work_id_format_is_enforced(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO core.works (work_id, publication_date, publication_year, type, first_run_id, last_run_id) "
            "VALUES ('not-an-id', '2024-01-01', 2024, 'article', 1, 1)"
        )
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/integration/test_migrations.py -v`
Expected: 3 个测试 FAIL：两个是 `assert … == EXPECTED_RELATIONS`（缺少 core/bridge 表），一个是 `psycopg.errors.UndefinedTable: relation "core.works" does not exist`

- [ ] **Step 3: 写迁移 0002**

创建 `sql/migrations/versions/0002_core_bridge_schema.py`：

```python
"""core and bridge schemas for OpenAlex works, plus per-work recall provenance

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-22
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = [
    "CREATE SCHEMA core",
    "CREATE SCHEMA bridge",
    # ---- core dimensions -------------------------------------------------------------------
    """
    CREATE TABLE core.countries (
        country_code char(2) PRIMARY KEY,
        name         text
    )
    """,
    """
    CREATE TABLE core.sources (
        source_id              text PRIMARY KEY,
        display_name           text NOT NULL,
        type                   text,
        issn_l                 text,
        host_organization_name text
    )
    """,
    """
    CREATE TABLE core.topics (
        topic_id      text PRIMARY KEY,
        display_name  text NOT NULL,
        subfield_id   text,
        subfield_name text,
        field_id      text,
        field_name    text,
        domain_id     text,
        domain_name   text
    )
    """,
    """
    CREATE TABLE core.keywords (
        keyword_id   text PRIMARY KEY,
        display_name text NOT NULL
    )
    """,
    """
    CREATE TABLE core.institutions (
        institution_id text PRIMARY KEY,
        display_name   text NOT NULL,
        ror_id         text,
        country_code   char(2) REFERENCES core.countries,
        type           text
    )
    """,
    """
    CREATE TABLE core.authors (
        author_id    text PRIMARY KEY,
        display_name text NOT NULL,
        orcid        text
    )
    """,
    # ---- core facts ------------------------------------------------------------------------
    """
    CREATE TABLE core.works (
        work_id                text PRIMARY KEY CHECK (work_id ~ '^W[0-9]+$'),
        doi                    text,
        title                  text,
        abstract               text,
        publication_date       date NOT NULL,
        publication_year       smallint NOT NULL,
        type                   text NOT NULL,
        language               text,
        source_id              text REFERENCES core.sources,
        is_oa                  boolean,
        oa_status              text,
        cited_by_count         integer NOT NULL DEFAULT 0,
        fwci                   numeric,
        citation_percentile    numeric,
        is_top_1pct            boolean,
        is_top_10pct           boolean,
        referenced_works_count integer NOT NULL DEFAULT 0,
        is_retracted           boolean NOT NULL DEFAULT false,
        openalex_updated_at    timestamptz,
        first_run_id           bigint NOT NULL REFERENCES meta.ingestion_runs,
        last_run_id            bigint NOT NULL REFERENCES meta.ingestion_runs,
        loaded_at              timestamptz NOT NULL DEFAULT now()
    )
    """,
    "COMMENT ON COLUMN core.works.doi IS 'Lower-cased DOI without the https://doi.org/ prefix; not unique in OpenAlex'",
    "CREATE INDEX works_publication_date_idx ON core.works (publication_date)",
    "CREATE INDEX works_doi_idx ON core.works (doi)",
    "CREATE INDEX works_source_idx ON core.works (source_id)",
    """
    CREATE TABLE core.work_yearly_citations (
        work_id        text REFERENCES core.works ON DELETE CASCADE,
        year           smallint,
        cited_by_count integer NOT NULL,
        PRIMARY KEY (work_id, year)
    )
    """,
    # ---- bridge tables ---------------------------------------------------------------------
    """
    CREATE TABLE bridge.work_authors (
        work_id                 text REFERENCES core.works ON DELETE CASCADE,
        author_seq              smallint,
        author_id               text REFERENCES core.authors,
        raw_author_name         text NOT NULL,
        author_position         text,
        is_corresponding        boolean NOT NULL DEFAULT false,
        raw_affiliation_strings text[] NOT NULL DEFAULT '{}',
        PRIMARY KEY (work_id, author_seq)
    )
    """,
    "COMMENT ON COLUMN bridge.work_authors.author_id IS 'NULL when OpenAlex has not disambiguated the author'",
    "CREATE INDEX work_authors_author_idx ON bridge.work_authors (author_id)",
    """
    CREATE TABLE bridge.authorship_institutions (
        work_id        text,
        author_seq     smallint,
        institution_id text REFERENCES core.institutions,
        PRIMARY KEY (work_id, author_seq, institution_id),
        FOREIGN KEY (work_id, author_seq) REFERENCES bridge.work_authors ON DELETE CASCADE
    )
    """,
    "CREATE INDEX authorship_institutions_institution_idx ON bridge.authorship_institutions (institution_id)",
    """
    CREATE TABLE bridge.authorship_countries (
        work_id      text,
        author_seq   smallint,
        country_code char(2) REFERENCES core.countries,
        PRIMARY KEY (work_id, author_seq, country_code),
        FOREIGN KEY (work_id, author_seq) REFERENCES bridge.work_authors ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE bridge.work_topics (
        work_id    text REFERENCES core.works ON DELETE CASCADE,
        topic_id   text REFERENCES core.topics,
        score      real NOT NULL,
        is_primary boolean NOT NULL,
        PRIMARY KEY (work_id, topic_id)
    )
    """,
    "CREATE INDEX work_topics_topic_idx ON bridge.work_topics (topic_id)",
    """
    CREATE TABLE bridge.work_keywords (
        work_id    text REFERENCES core.works ON DELETE CASCADE,
        keyword_id text REFERENCES core.keywords,
        score      real NOT NULL,
        PRIMARY KEY (work_id, keyword_id)
    )
    """,
    """
    CREATE TABLE bridge.work_references (
        work_id            text REFERENCES core.works ON DELETE CASCADE,
        referenced_work_id text NOT NULL CHECK (referenced_work_id ~ '^W[0-9]+$'),
        PRIMARY KEY (work_id, referenced_work_id)
    )
    """,
    "COMMENT ON COLUMN bridge.work_references.referenced_work_id IS "
    "'No foreign key: most cited works are outside the corpus'",
    "CREATE INDEX work_references_referenced_idx ON bridge.work_references (referenced_work_id)",
    """
    CREATE VIEW bridge.work_institutions AS
    SELECT DISTINCT work_id, institution_id
    FROM bridge.authorship_institutions
    """,
    """
    CREATE VIEW bridge.author_affiliations AS
    SELECT wa.author_id,
           ai.institution_id,
           min(w.publication_year)   AS first_year,
           max(w.publication_year)   AS last_year,
           count(DISTINCT w.work_id) AS works_count
    FROM bridge.work_authors wa
    JOIN bridge.authorship_institutions ai USING (work_id, author_seq)
    JOIN core.works w USING (work_id)
    WHERE wa.author_id IS NOT NULL
    GROUP BY wa.author_id, ai.institution_id
    """,
    # ---- recall provenance ("why is this work in the corpus?", architecture §5.2) ----------
    """
    CREATE TABLE meta.work_recall_hits (
        work_id         text REFERENCES core.works ON DELETE CASCADE,
        query_key       text NOT NULL,
        first_run_id    bigint NOT NULL REFERENCES meta.ingestion_runs,
        last_run_id     bigint NOT NULL REFERENCES meta.ingestion_runs,
        relevance_score real,
        PRIMARY KEY (work_id, query_key)
    )
    """,
]

DOWNGRADE = [
    "DROP TABLE meta.work_recall_hits",
    "DROP SCHEMA bridge CASCADE",
    "DROP SCHEMA core CASCADE",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
```

- [ ] **Step 4: 确认通过**

Run: `uv run pytest -v`
Expected: `7 passed`

```bash
uv run alembic upgrade head
uv run alembic current
```

Expected：`0002 (head)`

- [ ] **Step 5: 提交**

```bash
git add sql/migrations/versions/0002_core_bridge_schema.py tests/integration/test_migrations.py
git commit -m "feat: core and bridge schemas with recall provenance" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 召回查询配置

**Files:**
- Create: `src/scholarscope/ingestion/__init__.py`, `src/scholarscope/ingestion/recall.py`, `config/recall.toml`, `config/context.toml`, `tests/unit/test_recall.py`

**Interfaces:**
- Consumes: `scholarscope.db.PROJECT_ROOT`（仅测试用）
- Produces:
  - `THEMES = ("foundation_models", "llm", "rag", "agents")`，`CS_FIELD_ID = "17"`
  - `RecallQuery(key: str, theme: str, phrase: str)`（frozen dataclass）
  - `RecallConfig(from_date: date, types: tuple[str, ...], primary_field_ids: tuple[str, ...], queries: tuple[RecallQuery, ...])`，含 `from_dict(data: dict) -> RecallConfig`（校验主题、重复 key、短语中不得有 `,` `"` `|`）与 `to_dict() -> dict`
  - `load_recall_config(path: Path) -> RecallConfig`
  - `build_filter(config, query, *, to_date: date, field_ids: Sequence[str] | None = None, include_types: bool = True) -> str`；`field_ids=None` 使用配置里的领域，`()` 表示全领域

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_recall.py`：

```python
from datetime import date

import pytest

from scholarscope.db import PROJECT_ROOT
from scholarscope.ingestion.recall import CS_FIELD_ID, THEMES, RecallConfig, build_filter, load_recall_config

TO_DATE = date(2026, 9, 22)


def _config(**overrides) -> RecallConfig:
    data = {
        "from_date": "2019-01-01",
        "types": ["article", "preprint"],
        "primary_field_ids": [],
        "queries": [{"key": "rag.rag", "theme": "rag", "phrase": "retrieval augmented generation"}],
    }
    data.update(overrides)
    return RecallConfig.from_dict(data)


def test_build_filter_combines_phrase_dates_and_types():
    config = _config()
    assert build_filter(config, config.queries[0], to_date=TO_DATE) == (
        'title_and_abstract.search:"retrieval augmented generation",'
        "from_publication_date:2019-01-01,to_publication_date:2026-09-22,type:article|preprint"
    )


def test_build_filter_field_scope_and_type_toggle():
    config = _config(primary_field_ids=["17"])
    query = config.queries[0]
    assert build_filter(config, query, to_date=TO_DATE).endswith(",primary_topic.field.id:17")
    unrestricted = build_filter(config, query, to_date=TO_DATE, field_ids=(), include_types=False)
    assert "primary_topic" not in unrestricted
    assert "type:" not in unrestricted


@pytest.mark.parametrize("phrase", ["a, b", 'say "hi"', "a|b", "   "])
def test_rejects_phrases_that_break_filter_syntax(phrase):
    with pytest.raises(ValueError, match="phrase"):
        _config(queries=[{"key": "x", "theme": "rag", "phrase": phrase}])


def test_rejects_unknown_theme_and_duplicate_keys():
    with pytest.raises(ValueError, match="theme"):
        _config(queries=[{"key": "x", "theme": "robotics", "phrase": "robot"}])
    query = {"key": "x", "theme": "rag", "phrase": "rag"}
    with pytest.raises(ValueError, match="duplicate"):
        _config(queries=[query, query])


def test_round_trips_through_dict():
    config = _config()
    assert RecallConfig.from_dict(config.to_dict()) == config


def test_downloaded_corpus_is_rag_in_computer_science():
    config = load_recall_config(PROJECT_ROOT / "config" / "recall.toml")
    assert [q.key for q in config.queries] == ["rag.retrieval_augmented_generation"]
    assert config.primary_field_ids == (CS_FIELD_ID,)
    assert "conference-paper" in config.types


def test_context_phrases_cover_all_four_themes():
    config = load_recall_config(PROJECT_ROOT / "config" / "context.toml")
    assert {q.theme for q in config.queries} == set(THEMES)
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/unit/test_recall.py -v`
Expected: `ModuleNotFoundError: No module named 'scholarscope.ingestion'`

- [ ] **Step 3: 实现**

创建 `src/scholarscope/ingestion/__init__.py`：

```python
"""Data acquisition: OpenAlex API -> raw cache -> PostgreSQL."""
```

创建 `src/scholarscope/ingestion/recall.py`：

```python
"""Candidate-recall queries (architecture §5.2, stage 1) and the OpenAlex filters built from them."""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

THEMES = ("foundation_models", "llm", "rag", "agents")
CS_FIELD_ID = "17"  # OpenAlex field "Computer Science"
_FORBIDDEN_IN_PHRASE = (",", '"', "|")


@dataclass(frozen=True)
class RecallQuery:
    key: str
    theme: str
    phrase: str


@dataclass(frozen=True)
class RecallConfig:
    from_date: date
    types: tuple[str, ...]
    primary_field_ids: tuple[str, ...]
    queries: tuple[RecallQuery, ...]

    @classmethod
    def from_dict(cls, data: dict) -> RecallConfig:
        queries = tuple(RecallQuery(q["key"], q["theme"], q["phrase"]) for q in data["queries"])
        keys = [q.key for q in queries]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate recall query keys: {sorted(k for k in set(keys) if keys.count(k) > 1)}")
        for q in queries:
            if q.theme not in THEMES:
                raise ValueError(f"query {q.key!r}: unknown theme {q.theme!r}; expected one of {THEMES}")
            if not q.phrase.strip() or any(ch in q.phrase for ch in _FORBIDDEN_IN_PHRASE):
                raise ValueError(f"query {q.key!r}: phrase must be non-empty without , \" or |")
        return cls(
            from_date=date.fromisoformat(str(data["from_date"])),
            types=tuple(data.get("types", ())),
            primary_field_ids=tuple(str(f) for f in data.get("primary_field_ids", ())),
            queries=queries,
        )

    def to_dict(self) -> dict:
        return {
            "from_date": self.from_date.isoformat(),
            "types": list(self.types),
            "primary_field_ids": list(self.primary_field_ids),
            "queries": [{"key": q.key, "theme": q.theme, "phrase": q.phrase} for q in self.queries],
        }


def load_recall_config(path: Path) -> RecallConfig:
    with path.open("rb") as fh:
        return RecallConfig.from_dict(tomllib.load(fh))


def build_filter(
    config: RecallConfig,
    query: RecallQuery,
    *,
    to_date: date,
    field_ids: Sequence[str] | None = None,
    include_types: bool = True,
) -> str:
    """OpenAlex `filter` value for one recall query.

    `field_ids=None` uses the configured primary-topic fields; pass `()` to search all fields.
    """
    parts = [
        f'title_and_abstract.search:"{query.phrase}"',
        f"from_publication_date:{config.from_date.isoformat()}",
        f"to_publication_date:{to_date.isoformat()}",
    ]
    if include_types and config.types:
        parts.append("type:" + "|".join(config.types))
    ids = config.primary_field_ids if field_ids is None else tuple(field_ids)
    if ids:
        parts.append("primary_topic.field.id:" + "|".join(ids))
    return ",".join(parts)
```

创建 `config/recall.toml`（下载语料：RAG、仅 CS）：

```toml
# The corpus that is downloaded in full (architecture §1, §5.2 stage 1: candidate recall).
# Scope decision 2026-09-22 (option A): RAG as the single storyline, Computer Science only.
# The probe that day counted 15,921 matching works; FM / LLM / Agents stay as context
# (config/context.toml, counts only).
#
# The phrase is sent as an OpenAlex `title_and_abstract.search` phrase (stemmed, hyphen-insensitive).
# The acronym "RAG" alone is not used: it also means recombination-activating gene in biology.
# A phrase must not contain commas, double quotes or `|`.

from_date = "2019-01-01"

# OpenAlex added `conference-paper` as its own type; without it NeurIPS/ACL-style papers are lost.
types = ["article", "preprint", "conference-paper", "review"]

# OpenAlex field IDs on primary_topic. [] = all fields; ["17"] = Computer Science only.
primary_field_ids = ["17"]

[[queries]]
key = "rag.retrieval_augmented_generation"
theme = "rag"
phrase = "retrieval augmented generation"
```

创建 `config/context.toml`（背景短语，只供 `probe` 计数）：

```toml
# Macro-context phrases (architecture §1): probed only, never downloaded.
#   uv run scholarscope probe --recall config/context.toml
# stores yearly counts per phrase (all fields and Computer Science) in meta.recall_probes, so the
# RAG story can be framed against the whole Foundation Model / LLM / Agents landscape for ~$0.003.
# Same format as recall.toml; a phrase must not contain commas, double quotes or `|`.

from_date = "2019-01-01"
types = ["article", "preprint", "conference-paper", "review"]
primary_field_ids = []

[[queries]]
key = "fm.foundation_model"
theme = "foundation_models"
phrase = "foundation model"

[[queries]]
key = "fm.pretrained_language_model"
theme = "foundation_models"
phrase = "pretrained language model"

[[queries]]
key = "llm.large_language_model"
theme = "llm"
phrase = "large language model"

[[queries]]
key = "llm.chatgpt"
theme = "llm"
phrase = "ChatGPT"

[[queries]]
key = "rag.retrieval_augmented_generation"
theme = "rag"
phrase = "retrieval augmented generation"

[[queries]]
key = "agents.llm_agent"
theme = "agents"
phrase = "LLM agent"

[[queries]]
key = "agents.ai_agent"
theme = "agents"
phrase = "AI agent"

[[queries]]
key = "agents.agentic_ai"
theme = "agents"
phrase = "agentic AI"
```

- [ ] **Step 4: 确认通过**

Run: `uv run pytest tests/unit/test_recall.py -v`
Expected: `10 passed`

Run: `uv run pytest`
Expected: `17 passed`

- [ ] **Step 5: 提交**

```bash
git add src/scholarscope/ingestion config tests/unit/test_recall.py
git commit -m "feat: recall query config and OpenAlex filter builder" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: OpenAlex API 客户端

**Files:**
- Create: `src/scholarscope/ingestion/openalex_client.py`, `tests/unit/test_openalex_client.py`

**Interfaces:**
- Consumes: 无（传入 `httpx.Client`，测试用 `httpx.MockTransport`）
- Produces:
  - 异常：`OpenAlexError(RuntimeError)`；`RetryableResponseError(OpenAlexError)`（属性 `status_code`、`retry_after`）；`BudgetExhaustedError(OpenAlexError)`（429 且 `Retry-After` 大于 `max_retry_after_s`）
  - `Page(results: list[dict], next_cursor: str | None, count: int, cost_usd: float, raw: dict)`；`GroupCount(key: str, count: int)`
  - `OpenAlexClient(http: httpx.Client, api_key: str | None = None, *, max_attempts: int = 5, max_retry_after_s: float = 60.0, sleep: Callable[[float], None] = time.sleep)`
    - `fetch_works_page(filter_: str, cursor: str = "*", per_page: int = 100, sort: str | None = None) -> Page`（`sort` 为空时不发送，即 API 默认的相关度排序）
    - `group_works(filter_: str, group_by: str) -> tuple[list[GroupCount], float]`（桶名取 `key_display_name`，第二个返回值是该次调用费用）
  - 重试：5xx、429、`httpx.TransportError`；等待时间优先取 `Retry-After`，否则 `min(2**attempt, 30)` 秒；4xx（429 除外）不重试。

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_openalex_client.py`：

```python
import httpx
import pytest

from scholarscope.ingestion.openalex_client import BudgetExhaustedError, OpenAlexClient, OpenAlexError


def make_client(handler, **kwargs) -> tuple[OpenAlexClient, list[float]]:
    sleeps: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.openalex.org")
    return OpenAlexClient(http, sleep=sleeps.append, **kwargs), sleeps


def page_body(results, next_cursor=None, cost=0.001) -> dict:
    return {"meta": {"count": 42, "next_cursor": next_cursor, "cost_usd": cost}, "results": results}


def test_fetch_page_sends_filter_cursor_and_bearer_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=page_body([{"id": "https://openalex.org/W1"}], next_cursor="abc"))

    client, _ = make_client(handler, api_key="k3y")
    page = client.fetch_works_page("type:article", cursor="*", per_page=50, sort="publication_date:asc")

    assert page.results == [{"id": "https://openalex.org/W1"}]
    assert (page.next_cursor, page.count, page.cost_usd) == ("abc", 42, 0.001)
    request = seen[0]
    assert request.url.path == "/works"
    assert request.url.params["filter"] == "type:article"
    assert (request.url.params["cursor"], request.url.params["per_page"]) == ("*", "50")
    assert request.url.params["sort"] == "publication_date:asc"
    assert request.headers["Authorization"] == "Bearer k3y"
    assert "k3y" not in str(request.url)


def test_without_key_no_authorization_header():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=page_body([]))

    client, _ = make_client(handler)
    client.fetch_works_page("type:article")
    assert "Authorization" not in seen[0].headers
    assert "sort" not in seen[0].url.params


def test_retries_server_error_and_short_429_honouring_retry_after():
    responses = iter([
        httpx.Response(503),
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json=page_body([])),
    ])
    client, sleeps = make_client(lambda request: next(responses))
    assert client.fetch_works_page("type:article").results == []
    assert sleeps == [2.0, 7.0]


def test_retries_transport_errors():
    outcomes = iter([httpx.ConnectError("connection reset"), None])

    def handler(request):
        error = next(outcomes)
        if error:
            raise error
        return httpx.Response(200, json=page_body([]))

    client, sleeps = make_client(handler)
    assert client.fetch_works_page("type:article").results == []
    assert len(sleeps) == 1


def test_long_retry_after_means_budget_exhausted():
    client, sleeps = make_client(lambda request: httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(BudgetExhaustedError):
        client.fetch_works_page("type:article")
    assert sleeps == []


def test_gives_up_after_max_attempts():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    client, _ = make_client(handler, max_attempts=3)
    with pytest.raises(OpenAlexError):
        client.fetch_works_page("type:article")
    assert len(calls) == 3


def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, text="invalid filter")

    client, _ = make_client(handler)
    with pytest.raises(OpenAlexError, match="400"):
        client.fetch_works_page("nonsense")
    assert len(calls) == 1


def test_group_works_uses_display_keys_and_reports_cost():
    seen = []
    body = {
        "meta": {"count": 3, "cost_usd": 0.0001},
        "results": [],
        "group_by": [
            {"key": "https://openalex.org/types/article", "key_display_name": "article", "count": 2},
            {"key": "https://openalex.org/types/preprint", "key_display_name": "preprint", "count": 1},
        ],
    }

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=body)

    client, _ = make_client(handler)
    groups, cost = client.group_works("type:article|preprint", "type")
    assert [(g.key, g.count) for g in groups] == [("article", 2), ("preprint", 1)]
    assert cost == 0.0001
    assert seen[0].url.params["group_by"] == "type"
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/unit/test_openalex_client.py -v`
Expected: `ModuleNotFoundError: No module named 'scholarscope.ingestion.openalex_client'`

- [ ] **Step 3: 实现**

创建 `src/scholarscope/ingestion/openalex_client.py`：

```python
"""Minimal OpenAlex REST client: cursor paging, per-call cost tracking, retry honouring Retry-After."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt


class OpenAlexError(RuntimeError):
    """Non-retryable API error."""


class RetryableResponseError(OpenAlexError):
    def __init__(self, status_code: int, retry_after: float | None) -> None:
        super().__init__(f"OpenAlex returned {status_code}")
        self.status_code = status_code
        self.retry_after = retry_after


class BudgetExhaustedError(OpenAlexError):
    """HTTP 429 whose Retry-After is too long to wait for: the daily budget is spent."""


@dataclass(frozen=True)
class Page:
    results: list[dict]
    next_cursor: str | None
    count: int
    cost_usd: float
    raw: dict


@dataclass(frozen=True)
class GroupCount:
    key: str
    count: int


def _parse_retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class OpenAlexClient:
    def __init__(
        self,
        http: httpx.Client,
        api_key: str | None = None,
        *,
        max_attempts: int = 5,
        max_retry_after_s: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._max_attempts = max_attempts
        self._max_retry_after_s = max_retry_after_s
        self._sleep = sleep

    def fetch_works_page(self, filter_: str, cursor: str = "*", per_page: int = 100, sort: str | None = None) -> Page:
        params = {"filter": filter_, "cursor": cursor, "per_page": per_page}
        if sort:
            params["sort"] = sort
        body = self._get("/works", params)
        meta = body["meta"]
        return Page(
            results=body["results"],
            next_cursor=meta.get("next_cursor"),
            count=int(meta["count"]),
            cost_usd=float(meta.get("cost_usd") or 0.0),
            raw=body,
        )

    def group_works(self, filter_: str, group_by: str) -> tuple[list[GroupCount], float]:
        """Counts per `group_by` bucket, and the call's cost in USD."""
        body = self._get("/works", {"filter": filter_, "group_by": group_by, "per_page": 200})
        groups = [GroupCount(str(g.get("key_display_name") or g["key"]), int(g["count"])) for g in body["group_by"]]
        return groups, float(body["meta"].get("cost_usd") or 0.0)

    def _get(self, path: str, params: dict) -> dict:
        retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type((RetryableResponseError, httpx.TransportError)),
            sleep=self._sleep,
            reraise=True,
        )
        return retrying(self._get_once, path, params)

    def _get_once(self, path: str, params: dict) -> dict:
        response = self._http.get(path, params=params, headers=self._headers)
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            if response.status_code == 429 and retry_after is not None and retry_after > self._max_retry_after_s:
                raise BudgetExhaustedError(f"OpenAlex budget exhausted; retry after {retry_after:.0f}s")
            raise RetryableResponseError(response.status_code, retry_after)
        if response.status_code >= 400:
            raise OpenAlexError(f"OpenAlex returned {response.status_code}: {response.text[:300]}")
        return response.json()

    @staticmethod
    def _wait(state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, RetryableResponseError) and exc.retry_after is not None:
            return exc.retry_after
        return min(2.0**state.attempt_number, 30.0)
```

- [ ] **Step 4: 确认通过**

Run: `uv run pytest tests/unit/test_openalex_client.py -v`
Expected: `8 passed`

Run: `uv run pytest`
Expected: `25 passed`

- [ ] **Step 5: 提交**

```bash
git add src/scholarscope/ingestion/openalex_client.py tests/unit/test_openalex_client.py
git commit -m "feat: OpenAlex client with cursor paging, cost tracking and Retry-After retries" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: 记录转换与真实数据夹具

**Files:**
- Create: `tests/fixtures/openalex/works_page.json`（从计划附件复制）, `src/scholarscope/ingestion/transform.py`, `tests/unit/test_transform.py`

**Interfaces:**
- Consumes: 夹具 `works_page`（`tests/conftest.py`，Task 2）
- Produces:
  - `short_id(url) -> str | None`（`https://openalex.org/W1` → `W1`；`…/subfields/1702` → `1702`）、`normalize_doi(doi) -> str | None`（小写、去掉 `https://doi.org/`）、`reconstruct_abstract(inverted_index) -> str | None`、`parse_timestamp(value) -> datetime | None`（无时区按 UTC）
  - `WorkRows`（frozen dataclass）：`work: dict`、`source: dict | None`、`authors`、`institutions`、`countries: list[str]`、`topics`、`keywords`、`work_authors`、`authorship_institutions`、`authorship_countries`、`work_topics`、`work_keywords`、`references: list[str]`、`yearly_citations`、`relevance_score: float | None`。所有行字典的键与 Task 3 中对应表的列名一致；`work` 不含 `first_run_id`/`last_run_id`/`loaded_at`（由 loader 补）。
  - `transform_work(work: dict) -> WorkRows`

- [ ] **Step 1: 放入真实数据夹具**

```bash
mkdir -p tests/fixtures/openalex
cp docs/superpowers/plans/assets/2026-09-22-openalex-works-page.json tests/fixtures/openalex/works_page.json
```

夹具含 3 篇 2026-09-22 抓取的真实论文（已裁剪）：W4384071683（Nature，两位 Google 作者，顶层 `institutions` 为空）、W4404534210（首位作者 `author.id` 为 null）、W4389984066（arXiv 预印本，无机构、无参考文献，带 `relevance_score`）。不要重新抓取：测试断言依赖这些固定值。

- [ ] **Step 2: 写失败测试**

创建 `tests/unit/test_transform.py`：

```python
from datetime import UTC, date, datetime

from scholarscope.ingestion.transform import normalize_doi, reconstruct_abstract, short_id, transform_work


def by_id(page: dict, work_id: str) -> dict:
    return next(w for w in page["results"] if w["id"].endswith(work_id))


def test_short_id_and_doi_normalisation():
    assert short_id("https://openalex.org/W4384071683") == "W4384071683"
    assert short_id("https://openalex.org/subfields/1702") == "1702"
    assert short_id(None) is None
    assert normalize_doi("https://doi.org/10.1038/S41586-023-06291-2") == "10.1038/s41586-023-06291-2"
    assert normalize_doi("") is None


def test_reconstruct_abstract_orders_words_and_drops_label():
    index = {"Abstract": [0], "models": [2], "Large": [1], "big": [3, 5], "are": [4]}
    assert reconstruct_abstract(index) == "Large models big are big"
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None


def test_work_row_from_published_article(works_page):
    rows = transform_work(by_id(works_page, "W4384071683"))
    work = rows.work
    assert work["work_id"] == "W4384071683"
    assert work["doi"] == "10.1038/s41586-023-06291-2"
    assert work["title"] == "Large language models encode clinical knowledge"
    assert work["abstract"].startswith("Large language models (LLMs) have demonstrated")
    assert (work["publication_date"], work["publication_year"]) == (date(2023, 7, 12), 2023)
    assert (work["type"], work["source_id"]) == ("article", "S137773608")
    assert (work["is_oa"], work["oa_status"]) == (True, "hybrid")
    assert (work["cited_by_count"], work["referenced_works_count"]) == (3855, 91)
    assert (work["citation_percentile"], work["is_top_1pct"]) == (0.99998271, True)
    assert work["openalex_updated_at"] == datetime(2026, 9, 22, 6, 50, 58, 402377, tzinfo=UTC)
    assert rows.source == {
        "source_id": "S137773608", "display_name": "Nature", "type": "journal",
        "issn_l": "0028-0836", "host_organization_name": "Nature Portfolio",
    }
    assert rows.relevance_score is None


def test_institutions_come_from_authorships(works_page):
    raw = by_id(works_page, "W4384071683")
    assert raw["institutions"] == []  # the OpenAlex quirk transform.py works around
    rows = transform_work(raw)
    assert rows.institutions == [{
        "institution_id": "I1291425158", "display_name": "Google (United States)",
        "ror_id": "00njsd438", "country_code": "US", "type": "company",
    }]
    assert rows.authorship_institutions == [
        {"work_id": "W4384071683", "author_seq": 0, "institution_id": "I1291425158"},
        {"work_id": "W4384071683", "author_seq": 1, "institution_id": "I1291425158"},
    ]
    assert rows.countries == ["US"]
    assert [a["is_corresponding"] for a in rows.work_authors] == [True, True]


def test_author_without_openalex_id_keeps_the_authorship(works_page):
    rows = transform_work(by_id(works_page, "W4404534210"))
    first = rows.work_authors[0]
    assert (first["author_id"], first["raw_author_name"]) == (None, "Lei Huang")
    assert [a["author_id"] for a in rows.authors] == ["A5055989750"]
    assert rows.countries == ["CN"]


def test_preprint_without_affiliations_or_references(works_page):
    rows = transform_work(by_id(works_page, "W4389984066"))
    assert rows.work["type"] == "preprint"
    assert rows.work["fwci"] is None and rows.work["citation_percentile"] is None
    assert rows.institutions == [] and rows.authorship_institutions == [] and rows.references == []
    assert rows.relevance_score == 661.9417
    assert rows.work_authors[0]["raw_author_name"] == "Gao, Yunfan"


def test_topics_keywords_references_and_yearly_citations(works_page):
    rows = transform_work(by_id(works_page, "W4384071683"))
    assert [(t["topic_id"], t["is_primary"]) for t in rows.work_topics] == [("T10028", True), ("T11636", False)]
    assert rows.topics[0] == {
        "topic_id": "T10028", "display_name": "Topic Modeling",
        "subfield_id": "1702", "subfield_name": "Artificial Intelligence",
        "field_id": "17", "field_name": "Computer Science",
        "domain_id": "3", "domain_name": "Physical Sciences",
    }
    assert [k["keyword_id"] for k in rows.keywords] == ["computer-science", "benchmark"]
    assert rows.references == ["W1981208470", "W2042492924", "W2086519542"]
    assert rows.yearly_citations[0] == {"work_id": "W4384071683", "year": 2026, "cited_by_count": 1361}


def test_duplicate_references_are_collapsed(works_page):
    raw = by_id(works_page, "W4404534210")
    raw["referenced_works"] = raw["referenced_works"] + raw["referenced_works"][:1]
    assert transform_work(raw).references == ["W89714523", "W398859631"]
```

- [ ] **Step 3: 确认失败**

Run: `uv run pytest tests/unit/test_transform.py -v`
Expected: `ModuleNotFoundError: No module named 'scholarscope.ingestion.transform'`

- [ ] **Step 4: 实现**

创建 `src/scholarscope/ingestion/transform.py`：

```python
"""Pure functions turning one OpenAlex work record into rows for the core and bridge tables.

Two OpenAlex quirks this module handles (observed 2026-09-22):
- the top-level `institutions` list can be empty while authorships carry institutions,
  so institutions are always taken from `authorships[].institutions`;
- `authorships[].author.id` can be null (author not yet disambiguated).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi:")


@dataclass(frozen=True)
class WorkRows:
    work: dict
    source: dict | None
    authors: list[dict]
    institutions: list[dict]
    countries: list[str]
    topics: list[dict]
    keywords: list[dict]
    work_authors: list[dict]
    authorship_institutions: list[dict]
    authorship_countries: list[dict]
    work_topics: list[dict]
    work_keywords: list[dict]
    references: list[str]
    yearly_citations: list[dict]
    relevance_score: float | None


def short_id(url: str | None) -> str | None:
    """'https://openalex.org/W123' -> 'W123'; 'https://openalex.org/subfields/1702' -> '1702'."""
    if not url:
        return None
    return url.rstrip("/").rsplit("/", 1)[-1]


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    value = doi.strip().lower()
    for prefix in _DOI_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value or None


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Rebuild abstract text from OpenAlex's inverted index; drops a leading 'Abstract' label."""
    if not inverted_index:
        return None
    words = [word for _, word in sorted((pos, word) for word, positions in inverted_index.items() for pos in positions)]
    if words and words[0].lower() == "abstract":
        words = words[1:]
    text = " ".join(words).strip()
    return text or None


def parse_timestamp(value: str | None) -> datetime | None:
    """OpenAlex timestamps are naive UTC ('2026-09-22T06:50:58.402377')."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _topic_row(topic: dict) -> dict:
    subfield, field, domain = (topic.get(k) or {} for k in ("subfield", "field", "domain"))
    return {
        "topic_id": short_id(topic["id"]),
        "display_name": topic["display_name"],
        "subfield_id": short_id(subfield.get("id")),
        "subfield_name": subfield.get("display_name"),
        "field_id": short_id(field.get("id")),
        "field_name": field.get("display_name"),
        "domain_id": short_id(domain.get("id")),
        "domain_name": domain.get("display_name"),
    }


def transform_work(work: dict) -> WorkRows:
    work_id = short_id(work["id"])

    src = (work.get("primary_location") or {}).get("source")
    source = None
    if src and src.get("id"):
        source = {
            "source_id": short_id(src["id"]),
            "display_name": src.get("display_name") or short_id(src["id"]),
            "type": src.get("type"),
            "issn_l": src.get("issn_l"),
            "host_organization_name": src.get("host_organization_name"),
        }

    open_access = work.get("open_access") or {}
    percentile = work.get("citation_normalized_percentile") or {}
    work_row = {
        "work_id": work_id,
        "doi": normalize_doi(work.get("doi")),
        "title": work.get("title") or work.get("display_name"),
        "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "publication_date": date.fromisoformat(work["publication_date"]),
        "publication_year": work["publication_year"],
        "type": work["type"],
        "language": work.get("language"),
        "source_id": source["source_id"] if source else None,
        "is_oa": open_access.get("is_oa"),
        "oa_status": open_access.get("oa_status"),
        "cited_by_count": work.get("cited_by_count") or 0,
        "fwci": work.get("fwci"),
        "citation_percentile": percentile.get("value"),
        "is_top_1pct": percentile.get("is_in_top_1_percent"),
        "is_top_10pct": percentile.get("is_in_top_10_percent"),
        "referenced_works_count": work.get("referenced_works_count") or 0,
        "is_retracted": bool(work.get("is_retracted")),
        "openalex_updated_at": parse_timestamp(work.get("updated_date")),
    }

    authors: dict[str, dict] = {}
    institutions: dict[str, dict] = {}
    countries: set[str] = set()
    work_authors: list[dict] = []
    authorship_institutions: dict[tuple, dict] = {}
    authorship_countries: dict[tuple, dict] = {}
    for seq, authorship in enumerate(work.get("authorships") or []):
        author = authorship.get("author") or {}
        author_id = short_id(author.get("id"))
        if author_id:
            authors[author_id] = {
                "author_id": author_id,
                "display_name": author.get("display_name") or authorship.get("raw_author_name") or author_id,
                "orcid": short_id(author.get("orcid")),
            }
        work_authors.append({
            "work_id": work_id,
            "author_seq": seq,
            "author_id": author_id,
            "raw_author_name": authorship.get("raw_author_name") or author.get("display_name") or "",
            "author_position": authorship.get("author_position"),
            "is_corresponding": bool(authorship.get("is_corresponding")),
            "raw_affiliation_strings": list(authorship.get("raw_affiliation_strings") or []),
        })
        for inst in authorship.get("institutions") or []:
            institution_id = short_id(inst.get("id"))
            if not institution_id:
                continue
            country = (inst.get("country_code") or "").upper() or None
            if country:
                countries.add(country)
            institutions[institution_id] = {
                "institution_id": institution_id,
                "display_name": inst.get("display_name") or institution_id,
                "ror_id": short_id(inst.get("ror")),
                "country_code": country,
                "type": inst.get("type"),
            }
            authorship_institutions[(seq, institution_id)] = {
                "work_id": work_id, "author_seq": seq, "institution_id": institution_id,
            }
        for code in authorship.get("countries") or []:
            country = code.upper()
            countries.add(country)
            authorship_countries[(seq, country)] = {"work_id": work_id, "author_seq": seq, "country_code": country}

    primary_topic = work.get("primary_topic") or {}
    primary_topic_id = short_id(primary_topic.get("id"))
    topic_list = list(work.get("topics") or [])
    if primary_topic_id and primary_topic_id not in {short_id(t["id"]) for t in topic_list}:
        topic_list.append(primary_topic)
    topics = [_topic_row(t) for t in topic_list]
    work_topics = [
        {"work_id": work_id, "topic_id": short_id(t["id"]), "score": t.get("score") or 0.0,
         "is_primary": short_id(t["id"]) == primary_topic_id}
        for t in topic_list
    ]

    keyword_list = work.get("keywords") or []
    keywords = [{"keyword_id": short_id(k["id"]), "display_name": k["display_name"]} for k in keyword_list]
    work_keywords = [
        {"work_id": work_id, "keyword_id": short_id(k["id"]), "score": k.get("score") or 0.0} for k in keyword_list
    ]

    references = list(dict.fromkeys(ref for ref in map(short_id, work.get("referenced_works") or []) if ref))
    yearly_citations = [
        {"work_id": work_id, "year": c["year"], "cited_by_count": c["cited_by_count"]}
        for c in work.get("counts_by_year") or []
    ]

    return WorkRows(
        work=work_row,
        source=source,
        authors=list(authors.values()),
        institutions=list(institutions.values()),
        countries=sorted(countries),
        topics=topics,
        keywords=keywords,
        work_authors=work_authors,
        authorship_institutions=list(authorship_institutions.values()),
        authorship_countries=list(authorship_countries.values()),
        work_topics=work_topics,
        work_keywords=work_keywords,
        references=references,
        yearly_citations=yearly_citations,
        relevance_score=work.get("relevance_score"),
    )
```

- [ ] **Step 5: 确认通过**

Run: `uv run pytest tests/unit/test_transform.py -v`
Expected: `8 passed`

Run: `uv run pytest`
Expected: `33 passed`

- [ ] **Step 6: 提交**

```bash
git add tests/fixtures src/scholarscope/ingestion/transform.py tests/unit/test_transform.py
git commit -m "feat: transform OpenAlex works into core and bridge rows" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 采集运行记录与断点

**Files:**
- Create: `src/scholarscope/ingestion/runs.py`, `tests/integration/test_runs.py`

**Interfaces:**
- Consumes: `meta.ingestion_runs`、`meta.ingestion_checkpoints`（Task 2）；夹具 `db`
- Produces:
  - `SOURCE_ID = "openalex"`
  - `Checkpoint(query_key: str, next_cursor: str | None = "*", pages_done: int = 0, works_fetched: int = 0, is_exhausted: bool = False)`
  - `RunInfo(run_id: int, profile: str, params: dict, status: str)`
  - `start_run(conn, profile: str, params: dict) -> int`（状态 `running`）
  - `get_run(conn, run_id) -> RunInfo`（不存在时抛 `LookupError`）
  - `mark_running(conn, run_id)`、`finish_run(conn, run_id, status: str, message: str | None = None)`
  - `get_checkpoint(conn, run_id, query_key) -> Checkpoint`（无记录时返回默认值）
  - `record_page(conn, run_id, checkpoint, *, cost_usd: float, records: int)`（upsert 断点并累加 `requests_made`、`records_fetched`、`cost_usd`）
  - 这些函数不自行开事务，由调用方决定。

- [ ] **Step 1: 写失败测试**

创建 `tests/integration/test_runs.py`：

```python
from decimal import Decimal

import pytest

from scholarscope.ingestion import runs

pytestmark = pytest.mark.db


def test_run_lifecycle(db):
    run_id = runs.start_run(db, "smoke", {"to_date": "2026-09-22"})
    info = runs.get_run(db, run_id)
    assert (info.profile, info.status, info.params) == ("smoke", "running", {"to_date": "2026-09-22"})

    runs.finish_run(db, run_id, "partial", "cost limit reached")
    assert runs.get_run(db, run_id).status == "partial"

    runs.mark_running(db, run_id)
    row = db.execute(
        "SELECT status, finished_at, message FROM meta.ingestion_runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert row == ("running", None, None)


def test_unknown_run_raises(db):
    with pytest.raises(LookupError):
        runs.get_run(db, 999_999)


def test_checkpoints_default_then_track_pages(db):
    run_id = runs.start_run(db, "smoke", {})
    assert runs.get_checkpoint(db, run_id, "q") == runs.Checkpoint("q")

    runs.record_page(db, run_id, runs.Checkpoint("q", "abc", 1, 100, False), cost_usd=0.001, records=100)
    runs.record_page(db, run_id, runs.Checkpoint("q", None, 2, 150, True), cost_usd=0.001, records=50)

    assert runs.get_checkpoint(db, run_id, "q") == runs.Checkpoint("q", None, 2, 150, True)
    counters = db.execute(
        "SELECT requests_made, records_fetched, cost_usd FROM meta.ingestion_runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert counters == (2, 150, Decimal("0.0020"))
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/integration/test_runs.py -v`
Expected: `ImportError: cannot import name 'runs' from 'scholarscope.ingestion'`

- [ ] **Step 3: 实现**

创建 `src/scholarscope/ingestion/runs.py`：

```python
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
```

- [ ] **Step 4: 确认通过**

Run: `uv run pytest tests/integration/test_runs.py -v`
Expected: `3 passed`

Run: `uv run pytest`
Expected: `36 passed`

- [ ] **Step 5: 提交**

```bash
git add src/scholarscope/ingestion/runs.py tests/integration/test_runs.py
git commit -m "feat: ingestion run bookkeeping and resume checkpoints" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: 幂等加载器

**Files:**
- Create: `src/scholarscope/ingestion/loader.py`, `tests/integration/test_loader.py`

**Interfaces:**
- Consumes: `WorkRows`、`transform_work`（Task 6）；`runs.start_run`（Task 7）；Task 3 的表
- Produces: `load_works(conn, batch: Sequence[WorkRows], *, run_id: int, query_key: str) -> int`。维表 upsert（后到的值覆盖）；本批论文的 bridge 行先删后插，重复加载可反映作者、主题、引用的变化；`core.works.first_run_id` 保留首次，`last_run_id` 更新为本次；同一论文被多个查询命中时各记一条 `meta.work_recall_hits`。调用方负责事务。

- [ ] **Step 1: 写失败测试**

创建 `tests/integration/test_loader.py`：

```python
import pytest

from scholarscope.ingestion import runs
from scholarscope.ingestion.loader import load_works
from scholarscope.ingestion.transform import transform_work

pytestmark = pytest.mark.db

COUNTED = (
    "core.works", "core.authors", "core.institutions", "core.sources", "core.topics", "core.keywords",
    "core.countries", "core.work_yearly_citations", "bridge.work_authors", "bridge.authorship_institutions",
    "bridge.authorship_countries", "bridge.work_topics", "bridge.work_keywords", "bridge.work_references",
    "meta.work_recall_hits",
)


def counts(db) -> dict[str, int]:
    return {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in COUNTED}


def load(db, works: list[dict], run_id: int, query_key: str = "llm.large_language_model") -> int:
    with db.transaction():
        return load_works(db, [transform_work(w) for w in works], run_id=run_id, query_key=query_key)


def test_loads_fixture_page_into_every_table(db, works_page):
    run_id = runs.start_run(db, "test", {})
    assert load(db, works_page["results"], run_id) == 3
    assert counts(db) == {
        "core.works": 3, "core.authors": 5, "core.institutions": 3, "core.sources": 3, "core.topics": 4,
        "core.keywords": 5, "core.countries": 2, "core.work_yearly_citations": 9, "bridge.work_authors": 6,
        "bridge.authorship_institutions": 4, "bridge.authorship_countries": 4, "bridge.work_topics": 6,
        "bridge.work_keywords": 6, "bridge.work_references": 5, "meta.work_recall_hits": 3,
    }


def test_reloading_is_idempotent(db, works_page):
    first = runs.start_run(db, "test", {})
    second = runs.start_run(db, "test", {})
    load(db, works_page["results"], first)
    before = counts(db)

    load(db, works_page["results"], second)

    assert counts(db) == before
    assert db.execute("SELECT DISTINCT first_run_id, last_run_id FROM core.works").fetchall() == [(first, second)]


def test_reload_reflects_a_changed_record(db, works_page):
    run_id = runs.start_run(db, "test", {})
    load(db, works_page["results"], run_id)
    changed = works_page["results"][0]
    changed["cited_by_count"] = 9999
    changed["authorships"] = changed["authorships"][:1]
    changed["referenced_works"] = []

    load(db, [changed], run_id)

    def one(query: str):
        return db.execute(query).fetchone()[0]

    assert one("SELECT cited_by_count FROM core.works WHERE work_id = 'W4384071683'") == 9999
    assert one("SELECT count(*) FROM bridge.work_authors WHERE work_id = 'W4384071683'") == 1
    assert one("SELECT count(*) FROM bridge.authorship_institutions WHERE work_id = 'W4384071683'") == 1
    assert one("SELECT count(*) FROM bridge.work_references WHERE work_id = 'W4384071683'") == 0


def test_work_matched_by_two_queries_keeps_both_reasons(db, works_page):
    run_id = runs.start_run(db, "test", {})
    load(db, works_page["results"], run_id, "llm.large_language_model")
    load(db, works_page["results"], run_id, "rag.retrieval_augmented_generation")
    assert db.execute("SELECT count(*) FROM core.works").fetchone() == (3,)
    assert db.execute("SELECT count(*) FROM meta.work_recall_hits").fetchone() == (6,)


def test_views_derive_institutions_and_affiliations(db, works_page):
    load(db, works_page["results"], runs.start_run(db, "test", {}))
    assert db.execute(
        "SELECT count(*) FROM bridge.work_institutions WHERE work_id = 'W4384071683'"
    ).fetchone() == (1,)
    assert db.execute(
        "SELECT first_year, last_year, works_count FROM bridge.author_affiliations WHERE author_id = 'A5027454515'"
    ).fetchone() == (2023, 2023, 1)
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/integration/test_loader.py -v`
Expected: `ModuleNotFoundError: No module named 'scholarscope.ingestion.loader'`

- [ ] **Step 3: 实现**

创建 `src/scholarscope/ingestion/loader.py`：

```python
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
```

- [ ] **Step 4: 确认通过**

Run: `uv run pytest tests/integration/test_loader.py -v`
Expected: `5 passed`

Run: `uv run pytest`
Expected: `41 passed`

- [ ] **Step 5: 提交**

```bash
git add src/scholarscope/ingestion/loader.py tests/integration/test_loader.py
git commit -m "feat: idempotent loader for OpenAlex works" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: 原始缓存与采集流水线

**Files:**
- Create: `src/scholarscope/ingestion/raw_cache.py`, `src/scholarscope/ingestion/pipeline.py`, `tests/support.py`, `tests/unit/test_raw_cache.py`, `tests/integration/test_pipeline.py`

**Interfaces:**
- Consumes: `OpenAlexClient`、`BudgetExhaustedError`（Task 5）；`RecallConfig`、`build_filter`（Task 4）；`transform_work`（Task 6）；`runs`（Task 7）；`load_works`（Task 8）
- Produces:
  - `RawCache(root: Path)`：`page_path(run_id, query_key, page_no) -> Path`（`<root>/openalex/run-00001/<query_key>/page-00001.json.gz`）、`write_page(run_id, query_key, page_no, payload: dict) -> Path`（先写 `.tmp` 再原子替换）
  - `pipeline.MAX_PER_PAGE = 100`；`BY_RELEVANCE = "relevance_score:desc"`、`BY_DATE = "publication_date:asc"`；`Profile(name: str, max_works_per_query: int | None, max_cost_usd: float, sort: str)`；`PROFILES = {"smoke": Profile("smoke", 1000, 0.05, BY_RELEVANCE), "demo": Profile("demo", 5000, 0.50, BY_RELEVANCE), "standard": Profile("standard", None, 0.90, BY_DATE)}`
  - `IngestResult(run_id: int, status: str, works_fetched: int, cost_usd: float, message: str | None)`；`status` 为 `succeeded` / `partial`；异常时运行记为 `failed` 并重新抛出
  - `ingest(conn, client, cache, recall, profile, *, to_date: date) -> IngestResult`（`conn` 必须是 autocommit）；`resume(conn, client, cache, run_id) -> IngestResult`（从运行参数恢复召回配置、截止日期和档位，不读当前 `recall.toml`）
  - 测试支持 `tests/support.py`：`TWO_QUERY_RECALL_TOML: str`、`TWO_QUERY_RECALL: RecallConfig`、`FakeOpenAlex(pages_by_phrase, *, groups=None, cost_per_page=0.001)`，含 `.requests`、`.fail(phrase, cursor, status, headers=None)`、`.heal()`、`.handler`、`.http() -> httpx.Client`、`.client(**kwargs) -> OpenAlexClient`（`group_by` 支持供 Task 10 使用）

- [ ] **Step 1: 写测试替身和失败测试**

创建 `tests/support.py`：

```python
"""Shared test doubles: an in-memory OpenAlex /works endpoint and a two-query recall config."""

import re
import tomllib

import httpx

from scholarscope.ingestion.openalex_client import OpenAlexClient
from scholarscope.ingestion.recall import RecallConfig

TWO_QUERY_RECALL_TOML = """
from_date = "2019-01-01"
types = ["article"]
primary_field_ids = []

[[queries]]
key = "llm.large_language_model"
theme = "llm"
phrase = "large language model"

[[queries]]
key = "rag.retrieval_augmented_generation"
theme = "rag"
phrase = "retrieval augmented generation"
"""
TWO_QUERY_RECALL = RecallConfig.from_dict(tomllib.loads(TWO_QUERY_RECALL_TOML))

_PHRASE = re.compile(r'title_and_abstract\.search:"([^"]+)"')


class FakeOpenAlex:
    """Serves canned pages keyed by searched phrase; cursors are '*', 'p1', 'p2', ...

    Group-by requests return `groups[group_by]`, halved when the filter restricts the field.
    """

    def __init__(
        self,
        pages_by_phrase: dict[str, list[list[dict]]],
        *,
        groups: dict[str, list[tuple[str, int]]] | None = None,
        cost_per_page: float = 0.001,
    ) -> None:
        self.pages_by_phrase = pages_by_phrase
        self.groups = groups or {}
        self.cost_per_page = cost_per_page
        self.requests: list[dict[str, str]] = []
        self._failures: dict[tuple[str, str], tuple[int, dict[str, str]]] = {}

    def fail(self, phrase: str, cursor: str, status: int, headers: dict[str, str] | None = None) -> None:
        self._failures[(phrase, cursor)] = (status, headers or {})

    def heal(self) -> None:
        self._failures.clear()

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        phrase = _PHRASE.search(params["filter"]).group(1)
        cursor = params.get("cursor", "*")
        if (phrase, cursor) in self._failures:
            status, headers = self._failures[(phrase, cursor)]
            return httpx.Response(status, headers=headers)
        if "group_by" in params:
            divisor = 2 if "primary_topic.field.id" in params["filter"] else 1
            buckets = [
                {"key": key, "key_display_name": key, "count": count // divisor}
                for key, count in self.groups.get(params["group_by"], [])
            ]
            return httpx.Response(200, json={"meta": {"count": 0, "cost_usd": 0.0001}, "results": [], "group_by": buckets})
        pages = self.pages_by_phrase.get(phrase, [[]])
        index = 0 if cursor == "*" else int(cursor.removeprefix("p"))
        next_cursor = f"p{index + 1}" if index + 1 < len(pages) else None
        body = {
            "meta": {"count": sum(map(len, pages)), "next_cursor": next_cursor, "cost_usd": self.cost_per_page},
            "results": pages[index][: int(params["per_page"])],
        }
        return httpx.Response(200, json=body)

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), base_url="https://api.openalex.org")

    def client(self, **kwargs) -> OpenAlexClient:
        return OpenAlexClient(self.http(), sleep=lambda _seconds: None, **kwargs)
```

创建 `tests/unit/test_raw_cache.py`：

```python
import gzip
import json

from scholarscope.ingestion.raw_cache import RawCache


def test_write_page_round_trips_gzipped_json(tmp_path):
    cache = RawCache(tmp_path)
    path = cache.write_page(7, "rag.retrieval_augmented_generation", 3, {"results": [{"title": "检索增强生成"}]})

    assert path == tmp_path / "openalex" / "run-00007" / "rag.retrieval_augmented_generation" / "page-00003.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        assert json.load(fh)["results"][0]["title"] == "检索增强生成"
    assert list(path.parent.glob("*.tmp")) == []


def test_rewriting_a_page_replaces_it(tmp_path):
    cache = RawCache(tmp_path)
    cache.write_page(1, "q", 1, {"version": 1})
    path = cache.write_page(1, "q", 1, {"version": 2})
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        assert json.load(fh) == {"version": 2}
```

创建 `tests/integration/test_pipeline.py`：

```python
from datetime import date

import pytest

from scholarscope.ingestion import pipeline, runs
from scholarscope.ingestion.openalex_client import OpenAlexError
from scholarscope.ingestion.raw_cache import RawCache
from tests.support import TWO_QUERY_RECALL, FakeOpenAlex

pytestmark = pytest.mark.db

TO_DATE = date(2026, 9, 22)
UNCAPPED = pipeline.Profile("test", None, 1.0, pipeline.BY_DATE)


@pytest.fixture
def api(works_page) -> FakeOpenAlex:
    w1, w2, w3 = works_page["results"]
    return FakeOpenAlex({"large language model": [[w1, w2], [w3]], "retrieval augmented generation": [[w3]]})


def run(db, api, tmp_path, profile=UNCAPPED, **client_kwargs) -> pipeline.IngestResult:
    return pipeline.ingest(
        db, api.client(**client_kwargs), RawCache(tmp_path), TWO_QUERY_RECALL, profile, to_date=TO_DATE
    )


def scalar(db, query: str, *params):
    return db.execute(query, params).fetchone()[0]


def test_ingest_loads_every_page_and_records_the_run(db, api, tmp_path):
    result = run(db, api, tmp_path)

    assert (result.status, result.works_fetched) == ("succeeded", 4)
    assert result.cost_usd == pytest.approx(0.003)
    assert scalar(db, "SELECT count(*) FROM core.works") == 3
    assert scalar(db, "SELECT count(*) FROM meta.work_recall_hits") == 4
    assert db.execute(
        "SELECT status, requests_made, records_fetched FROM meta.ingestion_runs WHERE run_id = %s", (result.run_id,)
    ).fetchone() == ("succeeded", 3, 4)
    assert scalar(db, "SELECT bool_and(is_exhausted) FROM meta.ingestion_checkpoints WHERE run_id = %s", result.run_id)
    assert len(list(tmp_path.rglob("page-*.json.gz"))) == 3
    assert "to_publication_date:2026-09-22" in api.requests[0]["filter"]
    assert {r["sort"] for r in api.requests} == {"publication_date:asc"}


def test_second_run_updates_instead_of_duplicating(db, api, tmp_path):
    first = run(db, api, tmp_path)
    second = run(db, api, tmp_path)
    assert scalar(db, "SELECT count(*) FROM core.works") == 3
    assert db.execute("SELECT DISTINCT first_run_id, last_run_id FROM core.works").fetchall() == [
        (first.run_id, second.run_id)
    ]


def test_failed_run_resumes_from_its_checkpoint(db, api, tmp_path):
    api.fail("large language model", "p1", 500)
    with pytest.raises(OpenAlexError):
        run(db, api, tmp_path, max_attempts=2)
    run_id = scalar(db, "SELECT max(run_id) FROM meta.ingestion_runs")
    assert runs.get_run(db, run_id).status == "failed"
    assert runs.get_checkpoint(db, run_id, "llm.large_language_model").pages_done == 1
    assert scalar(db, "SELECT count(*) FROM core.works") == 2

    api.heal()
    api.requests.clear()
    result = pipeline.resume(db, api.client(), RawCache(tmp_path), run_id)

    assert (result.run_id, result.status) == (run_id, "succeeded")
    assert [r["cursor"] for r in api.requests] == ["p1", "*"]
    assert scalar(db, "SELECT count(*) FROM core.works") == 3


def test_cost_limit_stops_as_partial_and_can_resume(db, api, tmp_path):
    result = run(db, api, tmp_path, profile=pipeline.Profile("test", None, 0.001, pipeline.BY_DATE))
    assert result.status == "partial"
    assert f"--resume {result.run_id}" in result.message
    assert (result.works_fetched, len(api.requests)) == (2, 1)


def test_exhausted_daily_budget_is_partial_not_failed(db, api, tmp_path):
    api.fail("large language model", "*", 429, {"Retry-After": "86400"})
    result = run(db, api, tmp_path)
    assert result.status == "partial"
    assert runs.get_run(db, result.run_id).status == "partial"


def test_profile_cap_limits_works_per_query(db, api, tmp_path):
    result = run(db, api, tmp_path, profile=pipeline.Profile("test", 1, 1.0, pipeline.BY_RELEVANCE))
    assert (result.status, result.works_fetched) == ("succeeded", 2)
    assert [r["per_page"] for r in api.requests] == ["1", "1"]
    assert {r["sort"] for r in api.requests} == {"relevance_score:desc"}
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/unit/test_raw_cache.py tests/integration/test_pipeline.py -v`
Expected: 两个文件都在收集阶段报错：`ModuleNotFoundError: No module named 'scholarscope.ingestion.raw_cache'` 与 `ImportError: cannot import name 'pipeline' from 'scholarscope.ingestion'`

- [ ] **Step 3: 实现原始缓存**

创建 `src/scholarscope/ingestion/raw_cache.py`：

```python
"""Gzipped copies of raw API pages, so a schema change can be re-transformed without re-spending API budget."""

import gzip
import json
from pathlib import Path


class RawCache:
    def __init__(self, root: Path) -> None:
        self._root = root

    def page_path(self, run_id: int, query_key: str, page_no: int) -> Path:
        return self._root / "openalex" / f"run-{run_id:05d}" / query_key / f"page-{page_no:05d}.json.gz"

    def write_page(self, run_id: int, query_key: str, page_no: int, payload: dict) -> Path:
        path = self.page_path(run_id, query_key, page_no)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        tmp.replace(path)
        return path
```

- [ ] **Step 4: 实现流水线**

创建 `src/scholarscope/ingestion/pipeline.py`：

```python
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
```

- [ ] **Step 5: 确认通过**

Run: `uv run pytest tests/unit/test_raw_cache.py tests/integration/test_pipeline.py -v`
Expected: `8 passed`

Run: `uv run pytest`
Expected: `49 passed`

- [ ] **Step 6: 提交**

```bash
git add src/scholarscope/ingestion/raw_cache.py src/scholarscope/ingestion/pipeline.py tests/support.py tests/unit/test_raw_cache.py tests/integration/test_pipeline.py
git commit -m "feat: resumable, budget-aware OpenAlex ingestion pipeline with raw page cache" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: 范围探查

**Files:**
- Create: `src/scholarscope/ingestion/probe.py`, `tests/integration/test_probe.py`

**Interfaces:**
- Consumes: `OpenAlexClient.group_works`（Task 5）；`build_filter`、`CS_FIELD_ID`（Task 4）；`meta.recall_probes`（Task 2）；`FakeOpenAlex`（Task 9）
- Produces:
  - `FIELD_SCOPES = {"all": (), "cs": ("17",)}`；`DIMENSIONS = {"publication_year": True, "type": False}`（值表示是否套用配置的类型筛选；按类型分组时不套用，以便看到被排除的类型）
  - `run_probe(conn, client, recall, *, to_date: date, probed_at: datetime) -> float`（写入全部分组计数，返回 API 费用）
  - `probe_summary(conn, probed_at) -> list[tuple[str, str, int, int]]`：`(query_key, theme, all_fields, computer_science)`，用 SQL 汇总

- [ ] **Step 1: 写失败测试**

创建 `tests/integration/test_probe.py`：

```python
from datetime import UTC, date, datetime

import pytest

from scholarscope.ingestion import probe
from tests.support import TWO_QUERY_RECALL, FakeOpenAlex

pytestmark = pytest.mark.db


def test_probe_stores_bucket_counts_and_summarises_them_in_sql(db):
    api = FakeOpenAlex(
        {}, groups={"publication_year": [("2024", 10), ("2025", 20)], "type": [("article", 25), ("dataset", 5)]}
    )
    probed_at = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    cost = probe.run_probe(db, api.client(), TWO_QUERY_RECALL, to_date=date(2026, 9, 22), probed_at=probed_at)

    assert cost == pytest.approx(8 * 0.0001)  # 2 queries x 2 field scopes x 2 dimensions
    assert probe.probe_summary(db, probed_at) == [
        ("llm.large_language_model", "llm", 30, 15),
        ("rag.retrieval_augmented_generation", "rag", 30, 15),
    ]
    assert db.execute("SELECT count(*) FROM meta.recall_probes").fetchone() == (16,)
    year_filters = [r["filter"] for r in api.requests if r["group_by"] == "publication_year"]
    type_filters = [r["filter"] for r in api.requests if r["group_by"] == "type"]
    assert all("type:article" in f for f in year_filters)
    assert all("type:" not in f for f in type_filters)
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/integration/test_probe.py -v`
Expected: `ImportError: cannot import name 'probe' from 'scholarscope.ingestion'`

- [ ] **Step 3: 实现**

创建 `src/scholarscope/ingestion/probe.py`：

```python
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
```

- [ ] **Step 4: 确认通过**

Run: `uv run pytest tests/integration/test_probe.py -v`
Expected: `1 passed`

Run: `uv run pytest`
Expected: `50 passed`

- [ ] **Step 5: 提交**

```bash
git add src/scholarscope/ingestion/probe.py tests/integration/test_probe.py
git commit -m "feat: recall scope probe stored in meta.recall_probes" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: 数据质量检查

**Files:**
- Create: `src/scholarscope/quality/__init__.py`, `src/scholarscope/quality/checks.py`, `tests/integration/test_quality_checks.py`

**Interfaces:**
- Consumes: Task 3 的表；`load_works`、`transform_work`、`runs.start_run`（测试用）
- Produces:
  - `QualityCheck(name, description, sql, threshold: float | None)`；`CheckResult(name, failing_rows: int, total_rows: int, threshold: float | None, passed: bool | None)`，属性 `ratio`
  - `CHECKS`：质量门 `works_without_recall_hit`（0）、`works_date_out_of_scope`（0）、`works_missing_title`（≤1%）、`works_duplicate_doi`（≤1%）；仅记录的指标 `works_missing_abstract`、`authorships_missing_author_id`、`works_without_institution`、`institutions_missing_ror`、`references_outside_corpus`（`passed` 为 `NULL`）
  - `run_quality_checks(conn, run_id: int | None = None) -> list[CheckResult]`（在一个事务里写入 `meta.data_quality_checks`）

- [ ] **Step 1: 写失败测试**

创建 `tests/integration/test_quality_checks.py`：

```python
import pytest

from scholarscope.ingestion import runs
from scholarscope.ingestion.loader import load_works
from scholarscope.ingestion.transform import transform_work
from scholarscope.quality.checks import CHECKS, run_quality_checks

pytestmark = pytest.mark.db


def load_fixture(db, works: list[dict]) -> int:
    run_id = runs.start_run(db, "test", {})
    with db.transaction():
        load_works(db, [transform_work(w) for w in works], run_id=run_id, query_key="llm.large_language_model")
    return run_id


def test_checks_on_the_fixture_corpus(db, works_page):
    run_id = load_fixture(db, works_page["results"])

    results = {r.name: r for r in run_quality_checks(db, run_id)}

    assert results["works_without_recall_hit"].passed is True
    assert results["works_date_out_of_scope"].passed is True
    missing_author = results["authorships_missing_author_id"]
    assert (missing_author.failing_rows, missing_author.total_rows, missing_author.passed) == (1, 6, None)
    no_institution = results["works_without_institution"]
    assert (no_institution.failing_rows, no_institution.total_rows) == (1, 3)
    assert results["references_outside_corpus"].failing_rows == 5
    assert db.execute(
        "SELECT count(*) FROM meta.data_quality_checks WHERE run_id = %s", (run_id,)
    ).fetchone() == (len(CHECKS),)


def test_work_without_inclusion_reason_fails_the_gate(db, works_page):
    run_id = load_fixture(db, works_page["results"])
    db.execute("DELETE FROM meta.work_recall_hits WHERE work_id = 'W4389984066'")

    result = {r.name: r for r in run_quality_checks(db, run_id)}["works_without_recall_hit"]

    assert (result.failing_rows, result.passed) == (1, False)


def test_shared_doi_counts_every_work_involved(db, works_page):
    works_page["results"][1]["doi"] = works_page["results"][0]["doi"]
    run_id = load_fixture(db, works_page["results"])

    result = {r.name: r for r in run_quality_checks(db, run_id)}["works_duplicate_doi"]

    assert (result.failing_rows, result.total_rows, result.passed) == (2, 3, False)
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/integration/test_quality_checks.py -v`
Expected: `ModuleNotFoundError: No module named 'scholarscope.quality'`

- [ ] **Step 3: 实现**

创建 `src/scholarscope/quality/__init__.py`：

```python
"""Data-quality checks over the loaded corpus."""
```

创建 `src/scholarscope/quality/checks.py`：

```python
"""SQL data-quality checks over the loaded corpus, recorded in meta.data_quality_checks.

A check with a threshold is a gate (passed = failing/total <= threshold). A check without one is a
tracked metric for the evaluation contract (简历深扒 §7.1): coverage and missingness, reported as-is.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class QualityCheck:
    name: str
    description: str
    sql: str  # returns exactly one row: (failing_rows, total_rows)
    threshold: float | None


@dataclass(frozen=True)
class CheckResult:
    name: str
    failing_rows: int
    total_rows: int
    threshold: float | None
    passed: bool | None

    @property
    def ratio(self) -> float:
        return self.failing_rows / self.total_rows if self.total_rows else 0.0


CHECKS = (
    QualityCheck(
        "works_without_recall_hit",
        "Works with no recorded inclusion reason in meta.work_recall_hits",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM meta.work_recall_hits h WHERE h.work_id = w.work_id)), count(*) FROM core.works w",
        0.0,
    ),
    QualityCheck(
        "works_date_out_of_scope",
        "Works published before 2019-01-01 or after the current date",
        "SELECT count(*) FILTER (WHERE publication_date < DATE '2019-01-01' OR publication_date > CURRENT_DATE), "
        "count(*) FROM core.works",
        0.0,
    ),
    QualityCheck(
        "works_missing_title",
        "Works with a NULL or blank title",
        "SELECT count(*) FILTER (WHERE coalesce(btrim(title), '') = ''), count(*) FROM core.works",
        0.01,
    ),
    QualityCheck(
        "works_duplicate_doi",
        "Works whose DOI is shared with at least one other work",
        "SELECT coalesce(sum(n) FILTER (WHERE n > 1), 0), (SELECT count(*) FROM core.works) "
        "FROM (SELECT count(*) AS n FROM core.works WHERE doi IS NOT NULL GROUP BY doi) AS per_doi",
        0.01,
    ),
    QualityCheck(
        "works_missing_abstract",
        "Works without an abstract",
        "SELECT count(*) FILTER (WHERE abstract IS NULL), count(*) FROM core.works",
        None,
    ),
    QualityCheck(
        "authorships_missing_author_id",
        "Authorships whose author OpenAlex has not disambiguated",
        "SELECT count(*) FILTER (WHERE author_id IS NULL), count(*) FROM bridge.work_authors",
        None,
    ),
    QualityCheck(
        "works_without_institution",
        "Works with no institution on any authorship (common for preprints)",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM bridge.authorship_institutions ai WHERE ai.work_id = w.work_id)), count(*) "
        "FROM core.works w",
        None,
    ),
    QualityCheck(
        "institutions_missing_ror",
        "Institutions without a ROR ID (input to the ROR matching step)",
        "SELECT count(*) FILTER (WHERE ror_id IS NULL), count(*) FROM core.institutions",
        None,
    ),
    QualityCheck(
        "references_outside_corpus",
        "Reference edges whose cited work is not in core.works",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM core.works w WHERE w.work_id = r.referenced_work_id)), count(*) "
        "FROM bridge.work_references r",
        None,
    ),
)

INSERT_RESULT = (
    "INSERT INTO meta.data_quality_checks "
    "(run_id, check_name, description, failing_rows, total_rows, threshold, passed) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)


def run_quality_checks(conn: psycopg.Connection, run_id: int | None = None) -> list[CheckResult]:
    results = []
    with conn.transaction():
        for check in CHECKS:
            failing, total = (int(v) for v in conn.execute(check.sql).fetchone())
            passed = None if check.threshold is None else (total == 0 or failing / total <= check.threshold)
            conn.execute(INSERT_RESULT, (run_id, check.name, check.description, failing, total, check.threshold, passed))
            results.append(CheckResult(check.name, failing, total, check.threshold, passed))
    return results
```

- [ ] **Step 4: 确认通过**

Run: `uv run pytest tests/integration/test_quality_checks.py -v`
Expected: `3 passed`

Run: `uv run pytest`
Expected: `53 passed`

- [ ] **Step 5: 提交**

```bash
git add src/scholarscope/quality tests/integration/test_quality_checks.py
git commit -m "feat: SQL data-quality gates and metrics" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: 命令行入口与开发文档

**Files:**
- Create: `src/scholarscope/cli.py`, `tests/unit/test_cli.py`, `tests/integration/test_cli_end_to_end.py`, `docs/development.md`
- Modify: `pyproject.toml`（新增 `[project.scripts]`）

**Interfaces:**
- Consumes: 前面所有模块
- Produces:
  - 命令 `scholarscope migrate | probe [--recall PATH] | ingest (--profile smoke|demo|standard | --resume RUN_ID) [--recall PATH] | quality [--run RUN_ID]`
  - 退出码：`ingest` 成功 0、`partial` 2；`quality` 任一质量门失败时为 1
  - `build_parser() -> argparse.ArgumentParser`；`main(argv=None, *, settings: Settings | None = None, http: httpx.Client | None = None) -> int`（测试注入 settings 和假 HTTP）

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_cli.py`：

```python
import pytest

from scholarscope.cli import build_parser


def test_ingest_needs_exactly_one_of_profile_or_resume():
    parser = build_parser()
    assert parser.parse_args(["ingest", "--profile", "smoke"]).profile == "smoke"
    assert parser.parse_args(["ingest", "--resume", "12"]).resume == 12
    for argv in (["ingest"], ["ingest", "--profile", "smoke", "--resume", "1"], ["ingest", "--profile", "huge"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
```

创建 `tests/integration/test_cli_end_to_end.py`：

```python
import pytest

from scholarscope.cli import main
from tests.support import TWO_QUERY_RECALL_TOML, FakeOpenAlex

pytestmark = pytest.mark.db


@pytest.fixture
def cli_settings(settings, test_db, tmp_path):
    return settings.model_copy(update={"postgres_db": test_db, "raw_data_dir": tmp_path / "raw"})


@pytest.fixture
def recall_file(tmp_path):
    path = tmp_path / "recall.toml"
    path.write_text(TWO_QUERY_RECALL_TOML, encoding="utf-8")
    return path


def test_ingest_then_quality(db, works_page, cli_settings, recall_file, capsys):
    w1, w2, w3 = works_page["results"]
    api = FakeOpenAlex({"large language model": [[w1, w2]], "retrieval augmented generation": [[w3]]})

    exit_code = main(["ingest", "--profile", "smoke", "--recall", str(recall_file)], settings=cli_settings, http=api.http())
    assert exit_code == 0
    assert "succeeded, 3 works" in capsys.readouterr().out
    assert [r["per_page"] for r in api.requests] == ["100", "100"]  # full pages: smoke allows 1 000 per query
    assert {r["sort"] for r in api.requests} == {"relevance_score:desc"}

    run_id = db.execute("SELECT max(run_id) FROM meta.ingestion_runs").fetchone()[0]
    assert main(["quality", "--run", str(run_id)], settings=cli_settings) == 0
    assert "works_without_recall_hit" in capsys.readouterr().out


def test_probe_prints_summary(db, cli_settings, recall_file, capsys):
    api = FakeOpenAlex({}, groups={"publication_year": [("2025", 40)], "type": [("article", 40)]})

    assert main(["probe", "--recall", str(recall_file)], settings=cli_settings, http=api.http()) == 0

    out = capsys.readouterr().out
    assert "llm.large_language_model" in out and "rag.retrieval_augmented_generation" in out
    assert db.execute("SELECT count(*) FROM meta.recall_probes").fetchone() == (8,)
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/unit/test_cli.py tests/integration/test_cli_end_to_end.py -v`
Expected: `ModuleNotFoundError: No module named 'scholarscope.cli'`

- [ ] **Step 3: 实现 CLI 并注册命令**

创建 `src/scholarscope/cli.py`：

```python
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
```

在 `pyproject.toml` 的 `dependencies = [...]` 列表结束（`]`）之后、`[build-system]` 之前插入：

```toml
[project.scripts]
scholarscope = "scholarscope.cli:main"

```

```bash
uv sync
```

- [ ] **Step 4: 确认通过**

Run: `uv run pytest tests/unit/test_cli.py tests/integration/test_cli_end_to_end.py -v`
Expected: `3 passed`

Run: `uv run pytest`
Expected: `56 passed`

```bash
uv run scholarscope --help
uv run scholarscope migrate
```

Expected：帮助信息列出 `migrate, probe, ingest, quality`；第二条输出 `database schema is at head`。

- [ ] **Step 5: 写开发文档**

创建 `docs/development.md`：

````markdown
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

Exit codes: `ingest` returns 0 when the run succeeded and 2 when it stopped `partial`;
`quality` returns 1 when any gate fails.

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
````

- [ ] **Step 6: 提交**

```bash
git add src/scholarscope/cli.py pyproject.toml uv.lock tests/unit/test_cli.py tests/integration/test_cli_end_to_end.py docs/development.md
git commit -m "feat: scholarscope CLI (migrate, probe, ingest, quality) and developer quickstart" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: 真实 API 验收与架构文档同步

**Files:**
- Modify: `任务架构.md`（文档头“项目阶段”、§5.3、§19 事实面状态）

**Interfaces:**
- Consumes: `scholarscope` 命令（Task 12）
- Produces: 项目主库中的真实 RAG 语料（smoke，可选完整语料）；与实际运行一致的架构文档状态

本任务真实调用 OpenAlex，不是自动化测试。必做步骤约 $0.025；参考值取自 2026-09-22，会随 OpenAlex 更新缓慢变化。运行编号按步骤顺序为 1、2、3。

- [ ] **Step 1:（可选）配置 API Key**

在 `.env` 中设置 `OPENALEX_API_KEY=<你的 key>`（https://openalex.org/settings/api 免费获取）。必做步骤不需要 Key；Step 6 的完整语料有 Key 才能一次跑完。

- [ ] **Step 2: 规模探查**

```bash
uv run scholarscope probe
uv run scholarscope probe --recall config/context.toml
```

Expected：第一条输出 1 行，`rag.retrieval_augmented_generation` 全领域约 24,042、CS 约 15,921，`probe cost $0.0004`；第二条输出 8 行（参考上文背景短语表），`probe cost $0.0032`。保存输出，Step 7 要用。

- [ ] **Step 3: smoke 采集**

Run: `uv run scholarscope ingest --profile smoke`
Expected：`run 1: succeeded, 1000 works, $0.0100`，退出码 0，共 10 页。

- [ ] **Step 4: 质量检查**

Run: `uv run scholarscope quality --run 1`
Expected：4 个质量门全部 `PASS`，退出码 0。参考指标：`works_missing_abstract` 约 13%、`works_without_institution` 约 26%、`authorships_missing_author_id` 约 7%、`references_outside_corpus` 约 93%。

- [ ] **Step 5: 用 SQL 抽查并验证重跑幂等**

```bash
docker compose exec postgres psql -U scholarscope -d scholarscope -c "SELECT publication_year, count(*) FROM core.works GROUP BY 1 ORDER BY 1"
docker compose exec postgres psql -U scholarscope -d scholarscope -c "SELECT count(*) AS works FROM core.works"
uv run scholarscope ingest --profile smoke
docker compose exec postgres psql -U scholarscope -d scholarscope -c "SELECT count(*) AS works, count(*) FILTER (WHERE last_run_id = 2) AS refreshed FROM core.works"
```

Expected：年份在 2019–2026；works 约 995（小于 1000 的原因见“数据怪癖”第 7 条）；第二次 smoke 为 `run 2: succeeded`。works 总数只会因相关度漂移增加少量论文，不会翻倍；`refreshed` 约等于第二次运行的去重论文数。这组数字就是《简历深扒》§7.1 要求的“ETL 重跑幂等性”证据。

- [ ] **Step 6:（可选）下载完整 RAG 语料**

Run: `uv run scholarscope ingest --profile standard`
Expected（有 Key）：约 11 分钟后输出 `run 3: succeeded, <约 15,9xx> works, $<约 0.16>`。
Expected（无 Key）：花到约 $0.10 时输出 `run 3: partial …; resume with --resume 3`，退出码 2；第二天运行 `uv run scholarscope ingest --resume 3` 完成。

完成后核对完整性与质量：

```bash
docker compose exec postgres psql -U scholarscope -d scholarscope -c "SELECT count(*) AS unique_works FROM meta.work_recall_hits WHERE last_run_id = 3"
uv run scholarscope quality --run 3
```

Expected：`unique_works` 等于 Step 2 中 CS 列的数字（按日期翻页，不应有重复或遗漏；若 OpenAlex 在两步之间新收录了论文，会有个位数差异），4 个质量门全部 `PASS`。

- [ ] **Step 7: 同步 `任务架构.md`**

1. 文档头“项目阶段”改为：`数据基座（Plan 1）已实现：仓库、Compose、schema、OpenAlex 采集与质量检查；RAG smoke 语料已入库`（若完成了 Step 6，改为“完整 RAG 语料已入库（<篇数>）”）。
2. §5.3 表格下的“规模探查结果”补一行实际采集结果：运行编号、档位、去重篇数、费用、耗时（来自 Step 3 / Step 6 的输出）。
3. §19“当前事实面状态”表：“代码”一行改为 `changed-and-verified`，说明写 `Plan 1 数据基座：56 项自动化测试通过；真实 API smoke 运行成功（<篇数>、<费用>）`；“工作区”一行补充 Git 仓库已初始化（分支 `feat/data-foundation`）。

- [ ] **Step 8: 提交**

```bash
git add 任务架构.md
git commit -m "docs: record Plan 1 verification results in the architecture" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

之后使用 superpowers:finishing-a-development-branch 决定合并到 `main` 还是建 PR（远端仓库尚未配置，属于团队决策）。

---

## 完成标准

- `docker compose up -d --wait` 后 `uv run pytest` 显示 `56 passed`；`uv run pytest -m "not db"` 在不启动数据库时显示 `32 passed, 24 deselected`。
- 真实 API smoke 运行 `succeeded`，4 个质量门全部 PASS，重跑后论文数不翻倍。
- `任务架构.md` 记录了实际运行结果与项目阶段。
- `git log` 中每个任务一个提交，`.env`、`data/raw/`、`简历深扒.md` 均不在版本库中。

## 本计划完成后需要团队决定的事项

1. **申请 OpenAlex Key**（免费）：完整 RAG 语料有 Key 可以一次下载完。
2. **RAG 子方向标签体系**：架构 §9.2 的多标签分类已改为 RAG 子方向，需要团队确定类别定义并抽样标注，这是 Plan 4（机器学习）的前置条件。
3. **下一份计划**：建议 Plan 2（ROR + World Bank），因为机构标准化和国家分析依赖它；也可以先做 Plan 3（分析层），尽早产出 PPT 图表。
4. **`简历深扒.md`**：§2.4“为什么选择这四个主题”等处仍按四主题叙述，需要按 RAG 主线改写（个人文档，本计划不改动）。
