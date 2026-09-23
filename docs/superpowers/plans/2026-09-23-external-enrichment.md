# ScholarScope AI 外部数据融合（Plan 2 / 8）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 ROR 机构信息和 World Bank 国家指标接入 `external` schema：机构获得标准化名称、类型、城市坐标和上下级关系，论文获得更完整的国家归属，国家获得可对照的宏观指标。

**Architecture:** 两个新数据源沿用 Plan 1 的模式——外部数据只经 `scholarscope.external` 进入 PostgreSQL，每批写入都挂在 `meta.ingestion_runs` 的一次运行下。ROR 走版本化数据转储（37 MB，CC0，Zenodo）而不是逐个 API 调用：只加载语料引用到的机构，用 SHA-256 记录用的是哪一版；OpenAlex 没给 ROR id 的机构和它完全没关联的原始单位字符串，再用 ROR 的 affiliation 匹配接口补，每个不同字符串只问一次并缓存。World Bank 的六个指标按原样存入长表，缺失值保留为 NULL，由质量指标如实报告。

**Tech Stack:** Python 3.13（uv）、PostgreSQL 17（Docker Compose）、psycopg 3.3.6、Alembic 1.20.0、httpx 0.28.1、pytest 9.1.1。不新增依赖：ROR 转储用标准库的 `zipfile` + `csv` 流式读取。

## Global Constraints

- Python `>=3.13,<3.14`，一律用 `uv run ...` 执行；**不新增任何依赖**，本计划用到的库全部已在 `uv.lock` 中。
- 外部数据只能经 `scholarscope.external` 进入数据库；分析、ML、界面和 Agent 只读 PostgreSQL（架构 §6）。
- `external` 的事实表（`ror_organizations`、`institution_crosswalk`、`affiliation_matches`、`country_profiles`、`country_indicators`）每行都带 `run_id`，指向 `meta.ingestion_runs` 里记录了版本号、校验和或 API 参数的那次运行；`ror_relationships` 随父机构整体重写、`indicators` 是静态代码对照表，这两张表不带 `run_id`。
- 数据库主机一律写 `127.0.0.1`，不写 `localhost`（Windows 上 `localhost` 先解析到 `::1`，Docker Desktop 不应答）。
- 迁移是 Alembic 版本文件里的原生 SQL，版本表为 `meta.schema_versions`。
- 缺失值保留为 NULL，不填充、不丢弃；覆盖率由 `scholarscope quality` 的指标报告（架构 §4.5、§7.1）。
- ROR 数据为 CC0，World Bank 数据为 CC BY 4.0；两者都在 `meta.data_sources` 里登记许可证。
- ROR 匹配只接受接口标记为 `chosen` 且分数不低于 0.8 的候选；宁可留空也不要错配。
- 开发机为 Windows 11；命令按 Git Bash 书写。
- AI Agent 创建的提交以 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` 结尾（下方提交命令已包含；实际由哪个模型执行就写哪个）。

## 本计划依据的实测事实（2026-09-23）

计划中的代码已在真实数据上完整跑通：106 项测试通过（Plan 1 的 62 项加本计划的 44 项），并对**语料数据库的副本**做了真实 API 验收，因此下面的数字都是实测值，不是估算。

**语料现状**（15,921 篇 RAG 论文，Plan 1 采集）：

| 事实 | 数值 | 对本计划的含义 |
|---|---:|---|
| 机构总数 | 5,684 | ROR 只需加载语料用到的这部分，不必存全部 141,528 家 |
| 已带 ROR id 的机构 | 5,667（99.7%） | OpenAlex 已经做完大部分工作，crosswalk 直接照搬 |
| 缺 ROR id 的机构 | 17 | 用机构名走 ROR 匹配接口，17 次调用 |
| 完全没有机构的论文 | 6,653（41.8%） | 国家分析的主要缺口 |
| 有原始单位字符串但未关联机构的作者位 | 2,230 | 去重后只有 796 个不同字符串，值得逐个匹配 |
| 其中可能因此获得国家归属的论文 | 470（上限） | 实际只拿回 86 篇：796 个字符串里 ROR 只能可信匹配 155 个 |
| 语料涉及国家 | 138 | |

**ROR 数据转储**（Zenodo 社区 `ror-data`，实测 2026-09-22 发布的 v2.13）：

| 事实 | 数值 |
|---|---|
| 压缩包 | 37.5 MB，CC0，DOI `10.5281/zenodo.22902037` |
| 解压后 | JSON 317.8 MB / CSV 55.4 MB — 本计划读 CSV，字段齐全且不必把 300 MB JSON 读进内存 |
| 机构总数 | 141,528（active 138,289、inactive 1,809、withdrawn 1,430） |
| 关系类型 | parent 22,409、related 7,801、child 6,212、successor 2,149、predecessor 465 |
| 每家机构的地点 | 恰好一个（实测无多地点行），含城市、经纬度、行政区、洲 |

CSV 里关系字段的格式是 `"child: url1, url2; parent: url3"`，名称字段带语言前缀（`"en: Google Research; no_lang_code: Googleplex"`），解析代码已按此实现并测试。

**affiliation 匹配的真实效果**：796 个不同字符串里只有 155 个（19.5%）得到 0.8 分以上的可信匹配，
最终让 86 篇论文拿到国家归属。匹配不上的主要是三类：缩写（`USC`、`NSYSU`）、ROR 没收录的小公司
（`Meshcapade`、`AI Forensics, Paris, France`），以及写法差一点的名字——`Ant Group, Beijing, China`
能匹配上，`AntGroup, Hangzhou, China` 就匹配不上。这一步因此是"锦上添花"而不是"雪中送炭"：它把机构
缺 ROR 从 17 家降到 1 家（这部分很干净），但 41.8% 没有机构的论文绝大多数仍然没有机构，因为它们的
作者位连原始单位字符串都没有。团队可以用 `--no-match` 跳过这一步，代价是少 86 篇的国家归属和 155 条
可复用的缓存。

**World Bank v2 API**（无需 Key，无费用；实测 217 个国家 + 78 个聚合条目，聚合的 `region.id` 为 `NA`）：

| 指标 | 2019–2026 覆盖情况 |
|---|---|
| `SP.POP.TOTL` 人口 | 217 国全覆盖到 2025 |
| `NY.GDP.MKTP.CD` GDP / `NY.GDP.PCAP.CD` 人均 GDP | 约 96% 覆盖到 2024，2025 年 186 国，2026 年为空 |
| `TX.VAL.TECH.MF.ZS` 高技术出口 | 约 76%，2024 年 125 国，之后为空 |
| `GB.XPD.RSDV.GD.ZS` 研发投入占 GDP | **只有 84–98 国/年，2024 年仅 30 国，2025 年起为空** |
| `SP.POP.SCIE.RD.P6` 每百万研究人员 | **约 76–84 国/年，2024 年仅 28 国，2025 年起为空** |

两个后果已写进设计：一是研发类指标滞后约 2 年，分析层必须用"截至第 T 年的最近一个可得值"而不是当年值；二是**台湾（TW）不在 World Bank 国家列表里**（香港、澳门在），而语料里有 156 篇台湾论文，所以国家指标图必然存在缺口，这由 `corpus_countries_without_profile` 指标显性报告。

**真实验收运行**（对语料数据库副本，2026-09-23）：

| 运行 | 结果 |
|---|---|
| `scholarscope ror --dump …`（run 4） | 5,747 家机构入库、5,683 家机构建立 crosswalk、796 个字符串匹配中 155 个、耗时 9 分 11 秒 |
| `scholarscope worldbank`（run 5） | 217 个国家、9,114 条观测（3,445 条无值）、耗时 9.8 秒 |
| 国家覆盖变化 | 有国家归属的论文 9,249 → 9,335 篇（+86）；机构上下级关系 1,665 对；缺 ROR 的机构 17 → 1 家 |

## 范围

**本计划包含：** `external` schema 及三个派生视图、ROR 转储加载与机构 crosswalk、ROR affiliation 匹配（机构名 + 原始单位字符串）、World Bank 国家档案与六个指标、四项新的质量指标、两个 CLI 命令、开发文档、真实验收。

**不包含（后续各自一份计划）：**

3. 分析层：物化视图、窗口函数指标、`EXPLAIN ANALYZE` 对比（`analytics` schema）
4. 机器学习：RAG 子方向标注与分类、向量与混合检索、趋势回测、影响力预测（`ml` schema）
5. 图：Neo4j 子图导出与图分析
6. Research Agent + Skills + 评测（`audit` schema）
7. Streamlit + Hallmark 界面
8. 交付：双语 README、CI、Release、PPT

**明确推迟的决定：** 是否接入 Crossref（架构 §4.4、§19）。语料里 1,715 篇（10.8%）没有 DOI，主要是预印本和会议论文；Crossref 只能补 DOI 已知的记录，对这批帮助有限。等 Plan 3 的分析层暴露出具体的元数据缺口后再决定，比现在为多源而多源更有依据。

## 执行前置条件

- Docker Desktop 已启动，Plan 1 的 PostgreSQL 服务在跑，语料已入库（`core.works` 15,921 行）。
- 仓库在 `main` 分支、工作区干净、`uv run pytest` 为 62 passed。
- 网络可访问 `zenodo.org`、`api.ror.org`、`api.worldbank.org`；三者都不需要 Key，也不产生费用。
- 磁盘：ROR 转储 37.5 MB 存到 `data/raw/ror/`（已 git 忽略）。
- 真实数据夹具随计划保存在 `docs/superpowers/plans/assets/`：`2026-09-23-ror-sample.zip`、`2026-09-23-zenodo-latest.json`、`2026-09-23-worldbank-countries.json`、`2026-09-23-worldbank-rd-indicator.json`。

## 文件结构

```text
sql/migrations/versions/0003_external_schema.py  external schema：ROR、crosswalk、国家指标 + 三个视图
src/scholarscope/external/ror.py                 Zenodo 发布解析、下载校验、CSV 流式读取与行解析
src/scholarscope/external/ror_match.py           ROR affiliation 匹配接口（只认 chosen + 阈值）
src/scholarscope/external/worldbank.py           World Bank v2 客户端：国家档案与指标序列
src/scholarscope/external/loader.py              external schema 的幂等写入
src/scholarscope/external/pipeline.py            两条富化流程 + 运行记录
src/scholarscope/quality/checks.py               新增四项外部数据覆盖率指标
src/scholarscope/cli.py                          新增 `ror` 与 `worldbank` 两个命令
tests/fixtures/ror/…  tests/fixtures/worldbank/… 真实数据夹具（ROR CC0 / World Bank CC BY）
tests/unit/test_ror.py  test_ror_match.py  test_worldbank.py
tests/integration/test_external_loader.py  test_external_pipeline.py
docs/development.md                              外部数据一节
```

---

### Task 1: external schema 与派生视图（迁移 0003）

**Files:**
- Create: `sql/migrations/versions/0003_external_schema.py`
- Modify: `tests/integration/test_migrations.py`（整体替换）

**Interfaces:**
- Consumes: 迁移 0002 的 `core.institutions`、`core.countries`、`bridge.work_authors`、`bridge.authorship_countries`、`meta.ingestion_runs`、`meta.data_sources`
- Produces:
  - `external.ror_organizations(ror_id PK CHECK '^0[0-9a-z]{8}$', display_name, status CHECK active/inactive/withdrawn, established, types text[], aliases text[], acronyms text[], country_code, country_name, subdivision_name, city, latitude, longitude, continent_code, website, wikidata_id, grid_id, run_id FK, loaded_at)`
  - `external.ror_relationships(ror_id FK, related_ror_id, relationship_type CHECK parent/child/related/successor/predecessor, PK 三列)`（`related_ror_id` 无外键：对端常在子集之外）
  - `external.institution_crosswalk(institution_id PK FK, ror_id FK, match_method CHECK openalex/affiliation_string, match_score, run_id FK, matched_at)`
  - `external.affiliation_matches(affiliation PK, ror_id FK 可空, match_score, run_id FK, matched_at)`——`ror_id` 为空表示"问过 ROR，没有可信匹配"
  - `external.country_profiles(country_code PK FK core.countries, iso3_code, name, region, income_level, capital_city, latitude, longitude, run_id FK)`
  - `external.indicators(indicator_code PK, name)`；`external.country_indicators(country_code FK, indicator_code FK, year, value 可空, run_id FK, PK 三列)`
  - 视图 `bridge.institution_relationships(institution_id, related_institution_id, relationship_type)`、`bridge.authorship_ror_institutions(work_id, author_seq, ror_id)`、`bridge.work_countries(work_id, country_code)`
  - `meta.data_sources` 新增 `ror`（CC0 1.0）与 `worldbank`（CC BY 4.0）两行

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
    "bridge.institution_relationships", "bridge.authorship_ror_institutions", "bridge.work_countries",
    "external.ror_organizations", "external.ror_relationships", "external.institution_crosswalk",
    "external.affiliation_matches", "external.country_profiles", "external.indicators",
    "external.country_indicators",
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
            "WHERE table_schema IN ('meta', 'core', 'bridge', 'external')"
        ).fetchall()
    return {r[0] for r in rows}


def test_upgrade_creates_every_table_and_view(settings, scratch_db):
    migrate(settings, scratch_db)
    assert relations(settings, scratch_db) == EXPECTED_RELATIONS
    with connect(settings, scratch_db) as conn:
        assert conn.execute("SELECT version_num FROM meta.schema_versions").fetchone() == ("0003",)
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
Expected: 2 个测试 FAIL（`assert … == EXPECTED_RELATIONS`，缺少 external 表与三个视图；版本仍为 `('0002',)`）

- [ ] **Step 3: 写迁移 0003**

创建 `sql/migrations/versions/0003_external_schema.py`：

```python
"""external schema: ROR organisations and relationships, institution crosswalk, World Bank country data

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = [
    "CREATE SCHEMA external",
    """
    INSERT INTO meta.data_sources (source_id, name, url, license, notes) VALUES
        ('ror', 'ROR (Research Organization Registry)', 'https://ror.org', 'CC0 1.0',
         'Versioned data dump from Zenodo (community ror-data), plus the affiliation matching API.'),
        ('worldbank', 'World Bank Open Data', 'https://data.worldbank.org', 'CC BY 4.0',
         'Country metadata and development indicators via the v2 REST API.')
    ON CONFLICT (source_id) DO UPDATE SET
        name = EXCLUDED.name, url = EXCLUDED.url, license = EXCLUDED.license, notes = EXCLUDED.notes
    """,
    # ---- ROR ---------------------------------------------------------------------------------
    """
    CREATE TABLE external.ror_organizations (
        ror_id           text PRIMARY KEY CHECK (ror_id ~ '^0[0-9a-z]{8}$'),
        display_name     text NOT NULL,
        status           text NOT NULL CHECK (status IN ('active', 'inactive', 'withdrawn')),
        established      smallint,
        types            text[] NOT NULL DEFAULT '{}',
        aliases          text[] NOT NULL DEFAULT '{}',
        acronyms         text[] NOT NULL DEFAULT '{}',
        country_code     char(2),
        country_name     text,
        subdivision_name text,
        city             text,
        latitude         double precision,
        longitude        double precision,
        continent_code   text,
        website          text,
        wikidata_id      text,
        grid_id          text,
        run_id           bigint NOT NULL REFERENCES meta.ingestion_runs,
        loaded_at        timestamptz NOT NULL DEFAULT now()
    )
    """,
    "COMMENT ON TABLE external.ror_organizations IS "
    "'Subset of the ROR dump: the organisations referenced by core.institutions'",
    "CREATE INDEX ror_organizations_country_idx ON external.ror_organizations (country_code)",
    """
    CREATE TABLE external.ror_relationships (
        ror_id            text REFERENCES external.ror_organizations ON DELETE CASCADE,
        related_ror_id    text NOT NULL,
        relationship_type text NOT NULL
            CHECK (relationship_type IN ('parent', 'child', 'related', 'successor', 'predecessor')),
        PRIMARY KEY (ror_id, related_ror_id, relationship_type)
    )
    """,
    "COMMENT ON COLUMN external.ror_relationships.related_ror_id IS "
    "'No foreign key: the other side is often outside the loaded subset'",
    """
    CREATE TABLE external.institution_crosswalk (
        institution_id text PRIMARY KEY REFERENCES core.institutions ON DELETE CASCADE,
        ror_id         text NOT NULL REFERENCES external.ror_organizations,
        match_method   text NOT NULL CHECK (match_method IN ('openalex', 'affiliation_string')),
        match_score    real,
        run_id         bigint NOT NULL REFERENCES meta.ingestion_runs,
        matched_at     timestamptz NOT NULL DEFAULT now()
    )
    """,
    "COMMENT ON COLUMN external.institution_crosswalk.match_score IS "
    "'NULL when OpenAlex supplied the ROR id; the ROR matching score when we matched a name'",
    "CREATE INDEX institution_crosswalk_ror_idx ON external.institution_crosswalk (ror_id)",
    """
    CREATE TABLE external.affiliation_matches (
        affiliation text PRIMARY KEY,
        ror_id      text REFERENCES external.ror_organizations,
        match_score real,
        run_id      bigint NOT NULL REFERENCES meta.ingestion_runs,
        matched_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    "COMMENT ON TABLE external.affiliation_matches IS "
    "'Raw affiliation string -> ROR, for authorships OpenAlex left unlinked. One row per distinct "
    "string so a repeated affiliation costs one API call; ror_id NULL records a confident no-match'",
    # ---- World Bank --------------------------------------------------------------------------
    """
    CREATE TABLE external.country_profiles (
        country_code  char(2) PRIMARY KEY REFERENCES core.countries,
        iso3_code     char(3) NOT NULL,
        name          text NOT NULL,
        region        text,
        income_level  text,
        capital_city  text,
        latitude      double precision,
        longitude     double precision,
        run_id        bigint NOT NULL REFERENCES meta.ingestion_runs
    )
    """,
    """
    CREATE TABLE external.indicators (
        indicator_code text PRIMARY KEY,
        name           text NOT NULL
    )
    """,
    """
    CREATE TABLE external.country_indicators (
        country_code   char(2) NOT NULL REFERENCES core.countries,
        indicator_code text NOT NULL REFERENCES external.indicators,
        year           smallint NOT NULL,
        value          double precision,
        run_id         bigint NOT NULL REFERENCES meta.ingestion_runs,
        PRIMARY KEY (country_code, indicator_code, year)
    )
    """,
    "COMMENT ON COLUMN external.country_indicators.value IS "
    "'NULL means the World Bank has no observation; R&D series lag ~2 years behind the corpus'",
    "CREATE INDEX country_indicators_series_idx ON external.country_indicators (indicator_code, year)",
    # ---- derived view (architecture §7.3) ------------------------------------------------------
    """
    CREATE VIEW bridge.institution_relationships AS
    SELECT source.institution_id,
           target.institution_id AS related_institution_id,
           relationship.relationship_type
    FROM external.ror_relationships relationship
    JOIN external.institution_crosswalk source ON source.ror_id = relationship.ror_id
    JOIN external.institution_crosswalk target ON target.ror_id = relationship.related_ror_id
    """,
    "COMMENT ON VIEW bridge.institution_relationships IS "
    "'ROR parent/child/related links, restricted to institution pairs that both appear in the corpus'",
    """
    CREATE VIEW bridge.authorship_ror_institutions AS
    SELECT author.work_id,
           author.author_seq,
           matched.ror_id
    FROM bridge.work_authors author
    CROSS JOIN LATERAL unnest(author.raw_affiliation_strings) AS raw(affiliation)
    JOIN external.affiliation_matches matched ON matched.affiliation = btrim(raw.affiliation)
    WHERE matched.ror_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM bridge.authorship_institutions linked
                      WHERE linked.work_id = author.work_id AND linked.author_seq = author.author_seq)
    """,
    "COMMENT ON VIEW bridge.authorship_ror_institutions IS "
    "'Affiliations recovered from raw strings; only for authorships OpenAlex did not link itself'",
    """
    CREATE VIEW bridge.work_countries AS
    SELECT DISTINCT work_id, country_code FROM bridge.authorship_countries
    UNION
    SELECT DISTINCT recovered.work_id, organization.country_code
    FROM bridge.authorship_ror_institutions recovered
    JOIN external.ror_organizations organization ON organization.ror_id = recovered.ror_id
    WHERE organization.country_code IS NOT NULL
    """,
    "COMMENT ON VIEW bridge.work_countries IS "
    "'Best-effort country attribution: OpenAlex authorship countries plus ROR-recovered affiliations'",
]

DOWNGRADE = [
    "DROP VIEW bridge.work_countries",
    "DROP VIEW bridge.authorship_ror_institutions",
    "DROP VIEW bridge.institution_relationships",
    "DROP SCHEMA external CASCADE",
    "DELETE FROM meta.data_sources source WHERE source.source_id IN ('ror', 'worldbank') "
    "AND NOT EXISTS (SELECT 1 FROM meta.ingestion_runs run WHERE run.source_id = source.source_id)",
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
Expected: `62 passed`

```bash
uv run alembic upgrade head
uv run alembic current
```

Expected：`0003 (head)`

- [ ] **Step 5: 提交**

```bash
git add sql/migrations/versions/0003_external_schema.py tests/integration/test_migrations.py
git commit -m "feat: external schema for ROR organisations and World Bank indicators" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: ROR 数据转储读取与解析

**Files:**
- Create: `src/scholarscope/external/__init__.py`, `src/scholarscope/external/ror.py`, `tests/fixtures/ror/ror_sample.zip`, `tests/fixtures/ror/zenodo_latest.json`, `tests/unit/test_ror.py`

**Interfaces:**
- Consumes: 无（只用 httpx 与标准库）
- Produces:
  - `RorDumpError(RuntimeError)`；`RorRelease(version, doi, file_name, url, size_bytes)`；`OrganizationRows(organization: dict, relationships: list[dict])`
  - `latest_release(http) -> RorRelease`（Zenodo 社区 `ror-data` 最新一条；无 zip 时抛 `RorDumpError`）
  - `sha256_of(path) -> str`；`download(http, release, directory) -> tuple[Path, str]`（文件已存在则跳过下载）
  - `iter_csv_rows(dump_path) -> Iterator[dict[str, str]]`（流式读 zip 里的 CSV）
  - `short_ror_id(url) -> str | None`；`parse_relationships(row) -> list[dict]`；`parse_organization(row) -> OrganizationRows`
  - `ROR_URL_PREFIX`、`RELATIONSHIP_TYPES`、`ZENODO_RECORDS_URL`

- [ ] **Step 1: 放入真实数据夹具**

```bash
mkdir -p tests/fixtures/ror
cp docs/superpowers/plans/assets/2026-09-23-ror-sample.zip tests/fixtures/ror/ror_sample.zip
cp docs/superpowers/plans/assets/2026-09-23-zenodo-latest.json tests/fixtures/ror/zenodo_latest.json
```

夹具是从 2026-09-22 的真实转储里裁出的 6 家机构（ROR 数据为 CC0，可再分发）：Google（有 7 个 child 和 1 个 parent、有别名）、Alphabet（Google 的 parent，用来验证视图只保留语料内的配对）、哈尔滨工业大学、华为、南洋理工大学，以及一家 `inactive` 的 Macmurray College（验证状态不会被丢掉）。`zenodo_latest.json` 是 Zenodo 记录接口的真实返回（已裁剪）。不要重新生成：测试断言依赖这些固定值。

- [ ] **Step 2: 写失败测试**

创建 `tests/unit/test_ror.py`：

```python
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from scholarscope.external.ror import (
    RorDumpError,
    download,
    iter_csv_rows,
    latest_release,
    parse_organization,
    parse_relationships,
    sha256_of,
    short_ror_id,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ror"
DUMP = FIXTURES / "ror_sample.zip"


def rows_by_id() -> dict[str, dict]:
    return {short_ror_id(row["id"]): row for row in iter_csv_rows(DUMP)}


def test_latest_release_reads_the_zenodo_record():
    payload = json.loads((FIXTURES / "zenodo_latest.json").read_text(encoding="utf-8"))
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    release = latest_release(http)

    assert release.version == "v2.13-2026-09-22"
    assert release.file_name == "v2.13-2026-09-22-ror-data.zip"
    assert release.doi == "10.5281/zenodo.22902037"
    assert release.url.endswith("/content")
    assert release.size_bytes > 0
    assert seen[0].url.params["communities"] == "ror-data"
    assert seen[0].url.params["sort"] == "newest"


def test_latest_release_without_hits_is_an_error():
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"hits": {"hits": []}})))
    with pytest.raises(RorDumpError, match="no releases"):
        latest_release(http)


def test_download_writes_the_file_once_and_reports_its_checksum(tmp_path):
    content = DUMP.read_bytes()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=content)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    release = type("R", (), {"file_name": "dump.zip", "url": "https://example.test/dump.zip"})()

    path, digest = download(http, release, tmp_path)
    assert path == tmp_path / "dump.zip"
    assert digest == hashlib.sha256(content).hexdigest()
    assert list(tmp_path.glob("*.part")) == []

    again, digest_again = download(http, release, tmp_path)
    assert (again, digest_again) == (path, digest)
    assert len(calls) == 1  # the second call reuses the file on disk


def test_iter_csv_rows_streams_the_dump():
    ids = [short_ror_id(row["id"]) for row in iter_csv_rows(DUMP)]
    assert "00njsd438" in ids and "02e7b5302" in ids
    assert len(ids) == len(set(ids)) == 6


def test_iter_csv_rows_rejects_an_archive_without_csv(tmp_path):
    import zipfile

    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("readme.txt", "no data here")
    with pytest.raises(RorDumpError, match="no CSV member"):
        list(iter_csv_rows(broken))


def test_parse_organization_of_a_company():
    organization = parse_organization(rows_by_id()["00njsd438"]).organization
    assert organization == {
        "ror_id": "00njsd438",
        "display_name": "Google (United States)",
        "status": "active",
        "established": 1998,
        "types": ["company", "funder"],
        "aliases": ["Google Research", "Googleplex"],
        "acronyms": [],
        "country_code": "US",
        "country_name": "United States",
        "subdivision_name": "California",
        "city": "Mountain View",
        "latitude": 37.38605,
        "longitude": -122.08385,
        "continent_code": "NA",
        "website": "https://www.google.com/",
        "wikidata_id": "Q95",
        "grid_id": "grid.420451.6",
    }


def test_parse_organization_keeps_non_active_status():
    organization = parse_organization(rows_by_id()["04yw47259"]).organization
    assert organization["status"] == "inactive"
    assert organization["display_name"] == "Macmurray College"


def test_parse_relationships_splits_types_and_targets():
    relationships = parse_relationships(rows_by_id()["00njsd438"])
    by_type: dict[str, list[str]] = {}
    for row in relationships:
        assert row["ror_id"] == "00njsd438"
        by_type.setdefault(row["relationship_type"], []).append(row["related_ror_id"])
    assert by_type["parent"] == ["02e9yx751"]
    assert len(by_type["child"]) == 7
    assert "04d06q394" in by_type["child"]


def test_parse_relationships_of_an_organisation_without_any():
    assert parse_relationships(rows_by_id()["04yw47259"]) == []


def test_short_ror_id_and_checksum_helpers(tmp_path):
    assert short_ror_id("https://ror.org/00njsd438") == "00njsd438"
    assert short_ror_id(None) is None
    assert short_ror_id("") is None
    sample = tmp_path / "x.bin"
    sample.write_bytes(b"scholarscope")
    assert sha256_of(sample) == hashlib.sha256(b"scholarscope").hexdigest()
```

- [ ] **Step 3: 确认失败**

Run: `uv run pytest tests/unit/test_ror.py -v`
Expected: `ModuleNotFoundError: No module named 'scholarscope.external'`

- [ ] **Step 4: 实现**

创建 `src/scholarscope/external/__init__.py`：

```python
"""External data sources: ROR institution records and World Bank country indicators."""
```

创建 `src/scholarscope/external/ror.py`：

```python
"""The ROR data dump: resolve the latest Zenodo release, download it, and read its rows.

ROR publishes a versioned CC0 dump on Zenodo (141,528 organisations in v2.13). One 37 MB download
beats tens of thousands of API calls and is reproducible: version plus SHA-256 pin exactly which
records a run used. The dump's CSV is 55 MB against 318 MB for the JSON and carries every field we
need, so this module reads the CSV member.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ZENODO_RECORDS_URL = "https://zenodo.org/api/records"
ROR_URL_PREFIX = "https://ror.org/"
RELATIONSHIP_TYPES = ("parent", "child", "related", "successor", "predecessor")
_CHUNK = 1 << 20


class RorDumpError(RuntimeError):
    """The Zenodo release could not be resolved or the dump is not readable."""


@dataclass(frozen=True)
class RorRelease:
    version: str  # e.g. "v2.13-2026-09-22"
    doi: str
    file_name: str
    url: str
    size_bytes: int


@dataclass(frozen=True)
class OrganizationRows:
    organization: dict
    relationships: list[dict] = field(default_factory=list)


def latest_release(http: httpx.Client) -> RorRelease:
    """The newest release in Zenodo's `ror-data` community."""
    response = http.get(ZENODO_RECORDS_URL, params={"communities": "ror-data", "sort": "newest", "size": 1})
    response.raise_for_status()
    hits = response.json()["hits"]["hits"]
    if not hits:
        raise RorDumpError("Zenodo returned no releases in the ror-data community")
    record = hits[0]
    archives = [f for f in record.get("files", []) if f["key"].endswith(".zip")]
    if not archives:
        raise RorDumpError(f"Zenodo record {record.get('id')} has no .zip file")
    archive = archives[0]
    return RorRelease(
        version=archive["key"].removesuffix("-ror-data.zip"),
        doi=record.get("doi", ""),
        file_name=archive["key"],
        url=archive["links"]["self"],
        size_bytes=int(archive["size"]),
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(http: httpx.Client, release: RorRelease, directory: Path) -> tuple[Path, str]:
    """Download the dump unless it is already there; returns its path and SHA-256."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / release.file_name
    if not path.exists():
        tmp = path.with_suffix(".part")
        with http.stream("GET", release.url) as response, tmp.open("wb") as fh:
            response.raise_for_status()
            for chunk in response.iter_bytes(_CHUNK):
                fh.write(chunk)
        tmp.replace(path)
    return path, sha256_of(path)


def iter_csv_rows(dump_path: Path) -> Iterator[dict[str, str]]:
    """Yield the dump's CSV rows one at a time (the file is too large to hold in memory)."""
    with zipfile.ZipFile(dump_path) as archive:
        names = [n for n in archive.namelist() if n.endswith(".csv")]
        if not names:
            raise RorDumpError(f"{dump_path.name} contains no CSV member")
        with archive.open(names[0]) as fh:
            yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))


def short_ror_id(value: str | None) -> str | None:
    """'https://ror.org/00njsd438' -> '00njsd438'."""
    if not value:
        return None
    return value.rsplit("/", 1)[-1].strip() or None


def _split(value: str, separator: str = "; ") -> list[str]:
    return [part.strip() for part in value.split(separator) if part.strip()] if value else []


def _strip_language(name: str) -> str:
    """Names are prefixed with their language: 'en: Google Research', 'no_lang_code: Googleplex'."""
    head, separator, tail = name.partition(": ")
    if separator and (head == "no_lang_code" or (head.isalpha() and len(head) <= 3)):
        return tail.strip()
    return name.strip()


def _names(value: str) -> list[str]:
    return [stripped for stripped in (_strip_language(n) for n in _split(value)) if stripped]


def _float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_relationships(row: dict[str, str]) -> list[dict]:
    """'child: url1, url2; parent: url3' -> one row per (ror_id, related_ror_id, type)."""
    ror_id = short_ror_id(row["id"])
    parsed: dict[tuple[str, str], dict] = {}
    for group in _split(row.get("relationships", "")):
        label, separator, targets = group.partition(":")
        relationship_type = label.strip().lower()
        if not separator or relationship_type not in RELATIONSHIP_TYPES:
            continue
        for target in _split(targets, ","):
            related = short_ror_id(target)
            if related:
                parsed[(related, relationship_type)] = {
                    "ror_id": ror_id,
                    "related_ror_id": related,
                    "relationship_type": relationship_type,
                }
    return list(parsed.values())


def parse_organization(row: dict[str, str]) -> OrganizationRows:
    """One CSV row -> the `external.ror_organizations` row plus its relationship rows."""
    ror_id = short_ror_id(row["id"])
    display_name = row.get("names.types.ror_display", "").strip()
    organization = {
        "ror_id": ror_id,
        "display_name": display_name or ror_id,
        "status": row.get("status", "").strip(),
        "established": _int(row.get("established", "")),
        "types": _split(row.get("types", "")),
        "aliases": _names(row.get("names.types.alias", "")),
        "acronyms": _names(row.get("names.types.acronym", "")),
        "country_code": (row.get("locations.geonames_details.country_code") or "").strip().upper() or None,
        "country_name": row.get("locations.geonames_details.country_name", "").strip() or None,
        "subdivision_name": row.get("locations.geonames_details.country_subdivision_name", "").strip() or None,
        "city": row.get("locations.geonames_details.name", "").strip() or None,
        "latitude": _float(row.get("locations.geonames_details.lat", "")),
        "longitude": _float(row.get("locations.geonames_details.lng", "")),
        "continent_code": row.get("locations.geonames_details.continent_code", "").strip() or None,
        "website": row.get("links.type.website", "").strip() or None,
        "wikidata_id": (row.get("external_ids.type.wikidata.preferred") or "").strip()
        or next(iter(_split(row.get("external_ids.type.wikidata.all", ""), ";")), None),
        "grid_id": (row.get("external_ids.type.grid.preferred") or "").strip() or None,
    }
    return OrganizationRows(organization=organization, relationships=parse_relationships(row))
```

- [ ] **Step 5: 确认通过**

Run: `uv run pytest tests/unit/test_ror.py -v`
Expected: `10 passed`

Run: `uv run pytest`
Expected: `72 passed`

- [ ] **Step 6: 提交**

```bash
git add src/scholarscope/external tests/fixtures/ror tests/unit/test_ror.py
git commit -m "feat: read and parse the ROR data dump" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: ROR affiliation 匹配客户端

**Files:**
- Create: `src/scholarscope/external/ror_match.py`, `tests/unit/test_ror_match.py`

**Interfaces:**
- Consumes: `scholarscope.external.ror.short_ror_id`（Task 2）
- Produces:
  - `MATCH_URL = "https://api.ror.org/v2/organizations"`；`DEFAULT_MIN_SCORE = 0.8`
  - `AffiliationMatch(ror_id: str, score: float, matching_type: str)`
  - `match_affiliation(http, affiliation: str, *, min_score: float = DEFAULT_MIN_SCORE) -> AffiliationMatch | None`——只看接口标记 `chosen` 的候选；它分数不够就返回 `None`，不退而求其次；空字符串不发请求

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_ror_match.py`：

```python
import httpx

from scholarscope.external.ror_match import match_affiliation


def client(payload, seen: list | None = None) -> httpx.Client:
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def item(ror_id: str, score: float, chosen: bool) -> dict:
    return {
        "score": score,
        "chosen": chosen,
        "matching_type": "SINGLE SEARCH",
        "organization": {"id": f"https://ror.org/{ror_id}"},
    }


def test_returns_the_chosen_candidate_above_the_threshold():
    seen: list = []
    payload = {"items": [item("02e7b5302", 1.0, True), item("008pxsf13", 0.94, False)]}

    match = match_affiliation(client(payload, seen), "Nanyang Technological University, Singapore")

    assert match is not None
    assert (match.ror_id, match.score, match.matching_type) == ("02e7b5302", 1.0, "SINGLE SEARCH")
    assert seen[0].url.params["affiliation"] == "Nanyang Technological University, Singapore"


def test_low_scoring_chosen_candidate_is_rejected():
    payload = {"items": [item("008pxsf13", 0.62, True), item("02e7b5302", 0.55, False)]}
    assert match_affiliation(client(payload), "Technological University") is None


def test_no_chosen_candidate_means_no_match():
    payload = {"items": [item("008pxsf13", 0.94, False)]}
    assert match_affiliation(client(payload), "Some Unknown Lab") is None


def test_empty_items_and_blank_affiliation():
    seen: list = []
    assert match_affiliation(client({"items": []}, seen), "Nowhere Institute") is None
    assert match_affiliation(client({"items": []}, seen), "   ") is None
    assert len(seen) == 1  # a blank affiliation is not sent to the API


def test_threshold_is_configurable():
    payload = {"items": [item("008pxsf13", 0.7, True)]}
    assert match_affiliation(client(payload), "Partial Name") is None
    match = match_affiliation(client(payload), "Partial Name", min_score=0.65)
    assert match is not None and match.ror_id == "008pxsf13"
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/unit/test_ror_match.py -v`
Expected: `ModuleNotFoundError: No module named 'scholarscope.external.ror_match'`

- [ ] **Step 3: 实现**

创建 `src/scholarscope/external/ror_match.py`：

```python
"""ROR's affiliation-matching endpoint, for the institutions OpenAlex left without a ROR id.

The endpoint scores candidates and marks at most one `chosen`. Only that candidate is considered,
and only above a score threshold: a wrong institution is worse for country and institution analysis
than an admitted gap, which the quality metrics report either way.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from scholarscope.external.ror import short_ror_id

MATCH_URL = "https://api.ror.org/v2/organizations"
DEFAULT_MIN_SCORE = 0.8


@dataclass(frozen=True)
class AffiliationMatch:
    ror_id: str
    score: float
    matching_type: str


def match_affiliation(
    http: httpx.Client, affiliation: str, *, min_score: float = DEFAULT_MIN_SCORE
) -> AffiliationMatch | None:
    """The chosen ROR organisation for a raw affiliation string, or None when none is confident."""
    if not affiliation.strip():
        return None
    response = http.get(MATCH_URL, params={"affiliation": affiliation})
    response.raise_for_status()
    for item in response.json().get("items", []):
        if not item.get("chosen"):
            continue
        score = float(item.get("score") or 0.0)
        ror_id = short_ror_id((item.get("organization") or {}).get("id"))
        if ror_id and score >= min_score:
            return AffiliationMatch(ror_id=ror_id, score=score, matching_type=item.get("matching_type", ""))
        return None  # the endpoint chose this one; a weaker candidate is not a better answer
    return None
```

- [ ] **Step 4: 确认通过**

Run: `uv run pytest tests/unit/test_ror_match.py -v`
Expected: `5 passed`

Run: `uv run pytest`
Expected: `77 passed`

- [ ] **Step 5: 提交**

```bash
git add src/scholarscope/external/ror_match.py tests/unit/test_ror_match.py
git commit -m "feat: ROR affiliation matching with a score threshold" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: World Bank 客户端

**Files:**
- Create: `src/scholarscope/external/worldbank.py`, `tests/fixtures/worldbank/worldbank_countries.json`, `tests/fixtures/worldbank/worldbank_rd_indicator.json`, `tests/unit/test_worldbank.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `BASE_URL`、`PER_PAGE = 20000`、`INDICATORS`（六个指标代码到名称的映射，架构 §4.3）
  - `WorldBankError(RuntimeError)`；`CountryProfile(country_code, iso3_code, name, region, income_level, capital_city, latitude, longitude)`
  - `fetch_countries(http) -> list[CountryProfile]`（剔除 `region.id == "NA"` 的 78 个聚合条目）
  - `fetch_indicator(http, indicator_code, *, from_year, to_year) -> list[dict]`，每行 `{country_code, indicator_code, year: int, value: float | None}`，缺失值保留为 `None`
  - 两者都跟随分页；API 返回错误信封时抛 `WorldBankError`

- [ ] **Step 1: 放入真实数据夹具**

```bash
mkdir -p tests/fixtures/worldbank
cp docs/superpowers/plans/assets/2026-09-23-worldbank-countries.json tests/fixtures/worldbank/worldbank_countries.json
cp docs/superpowers/plans/assets/2026-09-23-worldbank-rd-indicator.json tests/fixtures/worldbank/worldbank_rd_indicator.json
```

前者是四个国家加一个聚合条目（"Africa Eastern and Southern"）的真实返回，后者是这四国 2019–2026 年研发投入指标的真实返回，28 行里有 12 行为 `null`——正是本计划必须如实保留的滞后。

- [ ] **Step 2: 写失败测试**

创建 `tests/unit/test_worldbank.py`：

```python
import json
from pathlib import Path

import httpx
import pytest

from scholarscope.external.worldbank import INDICATORS, WorldBankError, fetch_countries, fetch_indicator

FIXTURES = Path(__file__).parents[1] / "fixtures" / "worldbank"


def client(payload, seen: list | None = None) -> httpx.Client:
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_countries_drops_aggregates_and_maps_iso2():
    payload = json.loads((FIXTURES / "worldbank_countries.json").read_text(encoding="utf-8"))
    seen: list = []

    profiles = fetch_countries(client(payload, seen))

    assert {p.country_code for p in profiles} == {"CN", "US", "SG", "IN"}  # the aggregate row is gone
    singapore = next(p for p in profiles if p.country_code == "SG")
    assert (singapore.iso3_code, singapore.name) == ("SGP", "Singapore")
    assert singapore.income_level == "High income"
    assert singapore.region == "East Asia & Pacific"
    assert singapore.capital_city == "Singapore"
    assert singapore.latitude == pytest.approx(1.28941) and singapore.longitude == pytest.approx(103.85)
    assert seen[0].url.params["format"] == "json"


def test_fetch_indicator_keeps_missing_values_as_none():
    payload = json.loads((FIXTURES / "worldbank_rd_indicator.json").read_text(encoding="utf-8"))
    seen: list = []

    observations = fetch_indicator(client(payload, seen), "GB.XPD.RSDV.GD.ZS", from_year=2019, to_year=2026)

    assert len(observations) == 28  # 4 countries x 8 years, nothing dropped
    assert {o["indicator_code"] for o in observations} == {"GB.XPD.RSDV.GD.ZS"}
    assert all(isinstance(o["year"], int) for o in observations)
    china_2023 = next(o for o in observations if o["country_code"] == "CN" and o["year"] == 2023)
    assert china_2023["value"] == pytest.approx(2.57729)
    assert any(o["value"] is None for o in observations)  # the reporting lag, preserved
    assert seen[0].url.params["date"] == "2019:2026"
    assert seen[0].url.path.endswith("/country/all/indicator/GB.XPD.RSDV.GD.ZS")


def test_fetch_indicator_follows_pagination():
    pages = {
        "1": [{"page": 1, "pages": 2, "per_page": 1, "total": 2},
              [{"country": {"id": "CN"}, "date": "2024", "value": 1.5}]],
        "2": [{"page": 2, "pages": 2, "per_page": 1, "total": 2},
              [{"country": {"id": "US"}, "date": "2024", "value": 3.5}]],
    }
    requested: list[str] = []

    def handler(request):
        page = request.url.params["page"]
        requested.append(page)
        return httpx.Response(200, json=pages[page])

    observations = fetch_indicator(
        httpx.Client(transport=httpx.MockTransport(handler)), "SP.POP.TOTL", from_year=2024, to_year=2024
    )
    assert requested == ["1", "2"]
    assert [o["country_code"] for o in observations] == ["CN", "US"]


def test_error_payload_raises():
    payload = [{"message": [{"id": "120", "key": "Invalid value", "value": "The provided parameter value is not valid"}]}]
    with pytest.raises(WorldBankError, match="Invalid value"):
        fetch_countries(client(payload))


def test_indicator_catalogue_matches_the_architecture():
    assert set(INDICATORS) == {
        "NY.GDP.MKTP.CD",
        "NY.GDP.PCAP.CD",
        "SP.POP.TOTL",
        "GB.XPD.RSDV.GD.ZS",
        "SP.POP.SCIE.RD.P6",
        "TX.VAL.TECH.MF.ZS",
    }
    assert INDICATORS["SP.POP.SCIE.RD.P6"] == "Researchers in R&D (per million people)"
```

- [ ] **Step 3: 确认失败**

Run: `uv run pytest tests/unit/test_worldbank.py -v`
Expected: `ModuleNotFoundError: No module named 'scholarscope.external.worldbank'`

- [ ] **Step 4: 实现**

创建 `src/scholarscope/external/worldbank.py`：

```python
"""World Bank Open Data v2: country metadata and indicator series. No API key, no cost.

Coverage measured 2026-09-22 over 217 countries: population is complete through 2025, GDP ~96%
through 2024, but R&D spending and researchers-per-million only reach ~40% of countries and stop at
2023. Values are therefore stored as they come, NULLs included, and the analysis layer joins the
most recent year at or before the year it needs.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import httpx

BASE_URL = "https://api.worldbank.org/v2"
PER_PAGE = 20000

# Architecture §4.3. The name is stored so reports can label a series without a second lookup.
INDICATORS = {
    "NY.GDP.MKTP.CD": "GDP (current US$)",
    "NY.GDP.PCAP.CD": "GDP per capita (current US$)",
    "SP.POP.TOTL": "Population, total",
    "GB.XPD.RSDV.GD.ZS": "Research and development expenditure (% of GDP)",
    "SP.POP.SCIE.RD.P6": "Researchers in R&D (per million people)",
    "TX.VAL.TECH.MF.ZS": "High-technology exports (% of manufactured exports)",
}


class WorldBankError(RuntimeError):
    """The API returned an error payload or an unusable response."""


@dataclass(frozen=True)
class CountryProfile:
    country_code: str  # ISO 3166-1 alpha-2, matching core.countries
    iso3_code: str
    name: str
    region: str | None
    income_level: str | None
    capital_city: str | None
    latitude: float | None
    longitude: float | None


def _float(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _pages(http: httpx.Client, path: str, params: dict) -> Iterator[list]:
    """Yield each page's data array, following the envelope's page count."""
    page = 1
    while True:
        query = {"format": "json", "per_page": PER_PAGE, "page": page, **params}
        response = http.get(f"{BASE_URL}/{path}", params=query)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, list) or len(body) < 2:
            message = body[0].get("message") if isinstance(body, list) and body else body
            raise WorldBankError(f"World Bank error for {path}: {message}")
        envelope, data = body[0], body[1] or []
        yield data
        if page >= int(envelope.get("pages") or 1):
            return
        page += 1


def fetch_countries(http: httpx.Client) -> list[CountryProfile]:
    """Real countries only: the endpoint also returns 78 aggregates, which carry region id 'NA'."""
    profiles = []
    for data in _pages(http, "country", {}):
        for row in data:
            if (row.get("region") or {}).get("id") == "NA" or not row.get("iso2Code"):
                continue
            profiles.append(
                CountryProfile(
                    country_code=row["iso2Code"].strip().upper(),
                    iso3_code=row["id"].strip().upper(),
                    name=row["name"].strip(),
                    region=((row.get("region") or {}).get("value") or "").strip() or None,
                    income_level=((row.get("incomeLevel") or {}).get("value") or "").strip() or None,
                    capital_city=(row.get("capitalCity") or "").strip() or None,
                    latitude=_float(row.get("latitude")),
                    longitude=_float(row.get("longitude")),
                )
            )
    return profiles


def fetch_indicator(http: httpx.Client, indicator_code: str, *, from_year: int, to_year: int) -> list[dict]:
    """Observations for one indicator; a missing value stays NULL rather than being dropped."""
    observations = []
    for data in _pages(http, f"country/all/indicator/{indicator_code}", {"date": f"{from_year}:{to_year}"}):
        for row in data:
            country_code = (row.get("country") or {}).get("id", "").strip().upper()
            if not country_code or not row.get("date"):
                continue
            observations.append(
                {
                    "country_code": country_code,
                    "indicator_code": indicator_code,
                    "year": int(row["date"]),
                    "value": _float(row.get("value")),
                }
            )
    return observations
```

- [ ] **Step 5: 确认通过**

Run: `uv run pytest tests/unit/test_worldbank.py -v`
Expected: `5 passed`

Run: `uv run pytest`
Expected: `82 passed`

- [ ] **Step 6: 提交**

```bash
git add src/scholarscope/external/worldbank.py tests/fixtures/worldbank tests/unit/test_worldbank.py
git commit -m "feat: World Bank client for country profiles and indicators" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: external schema 的幂等写入

**Files:**
- Create: `src/scholarscope/external/loader.py`, `tests/integration/test_external_loader.py`

**Interfaces:**
- Consumes: `OrganizationRows`（Task 2）、`CountryProfile`（Task 4）、Task 1 的表；测试还用到 `scholarscope.ingestion` 的 `runs.start_run`、`loader.load_works`、`transform.transform_work` 和夹具 `db`、`works_page`
- Produces:
  - `wanted_ror_ids(conn) -> set[str]`；`unmatched_institutions(conn, limit=None) -> list[tuple[str, str]]`；`missing_ror_ids(conn, ror_ids) -> set[str]`
  - `load_ror_organizations(conn, batch, *, run_id) -> int`（机构 upsert，关系行按机构先删后插）
  - `link_crosswalk_from_openalex(conn, *, run_id) -> int`；`link_crosswalk_match(conn, institution_id, ror_id, score, *, run_id)`
  - `load_affiliation_matches(conn, results, *, run_id) -> int`（`results` 为 `(affiliation, AffiliationMatch | None)`）
  - `load_country_profiles(conn, profiles, *, run_id) -> int`（同时把国家名写进 `core.countries`）；`load_indicator_catalogue(conn, indicators) -> int`；`load_observations(conn, observations, *, run_id) -> int`（丢掉 `core.countries` 里没有的国家代码，避免外键失败）
  - 所有函数都不自行开事务，由调用方决定

- [ ] **Step 1: 写失败测试**

创建 `tests/integration/test_external_loader.py`：

```python
from pathlib import Path

import pytest

from scholarscope.external import loader
from scholarscope.external.ror import iter_csv_rows, parse_organization, short_ror_id
from scholarscope.external.worldbank import CountryProfile
from scholarscope.ingestion import runs
from scholarscope.ingestion.loader import load_works
from scholarscope.ingestion.transform import transform_work

pytestmark = pytest.mark.db

DUMP = Path(__file__).parents[1] / "fixtures" / "ror" / "ror_sample.zip"


@pytest.fixture
def corpus(db, works_page) -> int:
    """The three fixture works, so core.institutions holds Google, Harbin IT and Huawei."""
    run_id = runs.start_run(db, "test", {})
    with db.transaction():
        load_works(db, [transform_work(w) for w in works_page["results"]], run_id=run_id, query_key="rag")
    return run_id


def organizations(*ror_ids: str) -> list:
    wanted = set(ror_ids)
    return [parse_organization(row) for row in iter_csv_rows(DUMP) if short_ror_id(row["id"]) in wanted]


def test_load_ror_organizations_stores_names_location_and_relationships(db, corpus):
    loaded = loader.load_ror_organizations(db, organizations("00njsd438", "02e9yx751"), run_id=corpus)

    assert loaded == 2
    row = db.execute(
        "SELECT display_name, status, established, types, aliases, country_code, city, latitude, wikidata_id "
        "FROM external.ror_organizations WHERE ror_id = '00njsd438'"
    ).fetchone()
    assert row[:3] == ("Google (United States)", "active", 1998)
    assert row[3] == ["company", "funder"] and row[4] == ["Google Research", "Googleplex"]
    assert (row[5], row[6]) == ("US", "Mountain View")
    assert row[7] == pytest.approx(37.38605) and row[8] == "Q95"
    assert db.execute(
        "SELECT count(*) FROM external.ror_relationships WHERE ror_id = '00njsd438'"
    ).fetchone() == (8,)
    assert db.execute(
        "SELECT relationship_type FROM external.ror_relationships "
        "WHERE ror_id = '00njsd438' AND related_ror_id = '02e9yx751'"
    ).fetchone() == ("parent",)


def test_reloading_organizations_replaces_their_relationships(db, corpus):
    loader.load_ror_organizations(db, organizations("00njsd438"), run_id=corpus)
    db.execute(
        "INSERT INTO external.ror_relationships (ror_id, related_ror_id, relationship_type) "
        "VALUES ('00njsd438', '09fake0000', 'related')"
    )

    loader.load_ror_organizations(db, organizations("00njsd438"), run_id=corpus)

    assert db.execute(
        "SELECT count(*) FROM external.ror_relationships WHERE related_ror_id = '09fake0000'"
    ).fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM external.ror_organizations").fetchone() == (1,)


def test_crosswalk_links_institutions_that_openalex_already_matched(db, corpus):
    loader.load_ror_organizations(db, organizations("00njsd438", "01yqg2h08", "00cmhce21"), run_id=corpus)

    linked = loader.link_crosswalk_from_openalex(db, run_id=corpus)

    assert linked == 3
    assert db.execute(
        "SELECT ror_id, match_method, match_score FROM external.institution_crosswalk "
        "WHERE institution_id = 'I1291425158'"
    ).fetchone() == ("00njsd438", "openalex", None)
    assert loader.unmatched_institutions(db) == []


def test_crosswalk_skips_institutions_whose_ror_record_is_absent(db, corpus):
    loader.load_ror_organizations(db, organizations("00njsd438"), run_id=corpus)

    assert loader.link_crosswalk_from_openalex(db, run_id=corpus) == 1
    assert [row[0] for row in loader.unmatched_institutions(db)] == ["I204983213", "I2250955327"]


def test_name_matched_institution_is_recorded_with_its_score(db, corpus):
    loader.load_ror_organizations(db, organizations("02e7b5302"), run_id=corpus)
    db.execute(
        "INSERT INTO core.institutions (institution_id, display_name, ror_id) VALUES ('I99', 'NTU', NULL)"
    )

    loader.link_crosswalk_match(db, "I99", "02e7b5302", 0.93, run_id=corpus)

    row = db.execute(
        "SELECT ror_id, match_method, match_score FROM external.institution_crosswalk "
        "WHERE institution_id = 'I99'"
    ).fetchone()
    assert row[:2] == ("02e7b5302", "affiliation_string")
    assert row[2] == pytest.approx(0.93)


def test_institution_relationships_view_keeps_pairs_inside_the_corpus(db, corpus):
    loader.load_ror_organizations(db, organizations("00njsd438", "02e9yx751"), run_id=corpus)
    db.execute(
        "INSERT INTO core.institutions (institution_id, display_name, ror_id) "
        "VALUES ('I4210128969', 'Alphabet (United States)', '02e9yx751')"
    )
    loader.link_crosswalk_from_openalex(db, run_id=corpus)

    rows = db.execute(
        "SELECT institution_id, related_institution_id, relationship_type FROM bridge.institution_relationships "
        "ORDER BY institution_id"
    ).fetchall()

    assert ("I1291425158", "I4210128969", "parent") in rows
    assert ("I4210128969", "I1291425158", "child") in rows
    assert all(row[1] is not None for row in rows)  # targets outside the corpus never appear


def test_affiliation_matches_cache_records_hits_and_confident_misses(db, corpus, works_page):
    from scholarscope.external.ror_match import AffiliationMatch

    loader.load_ror_organizations(db, organizations("02e7b5302"), run_id=corpus)
    results = [
        ("Nanyang Technological University, Singapore", AffiliationMatch("02e7b5302", 1.0, "SINGLE SEARCH")),
        ("A lab that ROR does not know", None),
    ]

    stored = loader.load_affiliation_matches(db, results, run_id=corpus)

    assert stored == 2
    assert db.execute(
        "SELECT ror_id, match_score FROM external.affiliation_matches WHERE affiliation LIKE 'Nanyang%'"
    ).fetchone() == ("02e7b5302", pytest.approx(1.0))
    assert db.execute(
        "SELECT ror_id, match_score FROM external.affiliation_matches WHERE affiliation LIKE 'A lab%'"
    ).fetchone() == (None, None)


def test_recovered_affiliations_give_a_work_its_country(db, works_page):
    """An authorship OpenAlex left unlinked gains a country through the ROR match."""
    from scholarscope.external.ror_match import AffiliationMatch

    preprint = next(w for w in works_page["results"] if w["id"].endswith("W4389984066"))
    preprint["authorships"][0]["raw_affiliation_strings"] = ["Nanyang Technological University, Singapore"]
    run_id = runs.start_run(db, "test", {})
    with db.transaction():
        load_works(db, [transform_work(preprint)], run_id=run_id, query_key="rag")
    loader.load_ror_organizations(db, organizations("02e7b5302"), run_id=run_id)
    loader.load_affiliation_matches(
        db,
        [("Nanyang Technological University, Singapore", AffiliationMatch("02e7b5302", 1.0, "SINGLE SEARCH"))],
        run_id=run_id,
    )

    assert db.execute(
        "SELECT work_id, author_seq, ror_id FROM bridge.authorship_ror_institutions"
    ).fetchall() == [("W4389984066", 0, "02e7b5302")]
    assert db.execute("SELECT work_id, country_code FROM bridge.work_countries").fetchall() == [
        ("W4389984066", "SG")
    ]


def test_country_profiles_fill_the_country_dimension(db, corpus):
    profiles = [
        CountryProfile("SG", "SGP", "Singapore", "East Asia & Pacific", "High income", "Singapore", 1.28, 103.8),
        CountryProfile("CN", "CHN", "China", "East Asia & Pacific", "Upper middle income", "Beijing", 39.9, 116.3),
    ]

    assert loader.load_country_profiles(db, profiles, run_id=corpus) == 2
    assert db.execute("SELECT name FROM core.countries WHERE country_code = 'SG'").fetchone() == ("Singapore",)
    assert db.execute(
        "SELECT iso3_code, region, income_level FROM external.country_profiles WHERE country_code = 'CN'"
    ).fetchone() == ("CHN", "East Asia & Pacific", "Upper middle income")


def test_observations_keep_nulls_and_skip_unknown_countries(db, corpus):
    loader.load_country_profiles(
        db, [CountryProfile("SG", "SGP", "Singapore", None, None, None, None, None)], run_id=corpus
    )
    loader.load_indicator_catalogue(db, {"SP.POP.TOTL": "Population, total"})
    observations = [
        {"country_code": "SG", "indicator_code": "SP.POP.TOTL", "year": 2024, "value": 6.04e6},
        {"country_code": "SG", "indicator_code": "SP.POP.TOTL", "year": 2026, "value": None},
        {"country_code": "ZZ", "indicator_code": "SP.POP.TOTL", "year": 2024, "value": 1.0},
    ]

    stored = loader.load_observations(db, observations, run_id=corpus)

    assert stored == 2  # the unknown country code is dropped rather than breaking the foreign key
    assert db.execute(
        "SELECT value FROM external.country_indicators WHERE country_code = 'SG' AND year = 2026"
    ).fetchone() == (None,)


def test_missing_ror_ids_reports_only_what_is_absent(db, corpus):
    loader.load_ror_organizations(db, organizations("00njsd438"), run_id=corpus)
    assert loader.missing_ror_ids(db, {"00njsd438", "02e7b5302"}) == {"02e7b5302"}
    assert loader.missing_ror_ids(db, set()) == set()
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/integration/test_external_loader.py -v`
Expected: `ImportError: cannot import name 'loader' from 'scholarscope.external'`

- [ ] **Step 3: 实现**

创建 `src/scholarscope/external/loader.py`：

```python
"""Idempotent writes into the `external` schema. The caller owns the transaction."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import psycopg

from scholarscope.external.ror import OrganizationRows
from scholarscope.external.worldbank import CountryProfile

ROR_COLUMNS = (
    "ror_id", "display_name", "status", "established", "types", "aliases", "acronyms", "country_code",
    "country_name", "subdivision_name", "city", "latitude", "longitude", "continent_code", "website",
    "wikidata_id", "grid_id",
)
PROFILE_COLUMNS = (
    "country_code", "iso3_code", "name", "region", "income_level", "capital_city", "latitude", "longitude",
)


def _upsert_sql(table: str, columns: Sequence[str], key: str) -> str:
    placeholders = ", ".join(f"%({column})s" for column in columns)
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns if column != key)
    return (
        f"INSERT INTO {table} ({', '.join(columns)}, run_id) VALUES ({placeholders}, %(run_id)s) "
        f"ON CONFLICT ({key}) DO UPDATE SET {updates}, run_id = EXCLUDED.run_id"
    )


UPSERT_ROR_ORGANIZATION = _upsert_sql("external.ror_organizations", ROR_COLUMNS, "ror_id") + ", loaded_at = now()"
INSERT_ROR_RELATIONSHIP = (
    "INSERT INTO external.ror_relationships (ror_id, related_ror_id, relationship_type) "
    "VALUES (%(ror_id)s, %(related_ror_id)s, %(relationship_type)s) ON CONFLICT DO NOTHING"
)
UPSERT_COUNTRY_PROFILE = _upsert_sql("external.country_profiles", PROFILE_COLUMNS, "country_code")
UPSERT_COUNTRY = (
    "INSERT INTO core.countries (country_code, name) VALUES (%(country_code)s, %(name)s) "
    "ON CONFLICT (country_code) DO UPDATE SET name = EXCLUDED.name"
)
UPSERT_INDICATOR = (
    "INSERT INTO external.indicators (indicator_code, name) VALUES (%(indicator_code)s, %(name)s) "
    "ON CONFLICT (indicator_code) DO UPDATE SET name = EXCLUDED.name"
)
UPSERT_OBSERVATION = (
    "INSERT INTO external.country_indicators (country_code, indicator_code, year, value, run_id) "
    "VALUES (%(country_code)s, %(indicator_code)s, %(year)s, %(value)s, %(run_id)s) "
    "ON CONFLICT (country_code, indicator_code, year) DO UPDATE SET "
    "value = EXCLUDED.value, run_id = EXCLUDED.run_id"
)
# OpenAlex already carries a ROR id for most institutions; that is the highest-confidence link.
INSERT_CROSSWALK_FROM_OPENALEX = (
    "INSERT INTO external.institution_crosswalk (institution_id, ror_id, match_method, match_score, run_id) "
    "SELECT institution.institution_id, institution.ror_id, 'openalex', NULL, %(run_id)s "
    "FROM core.institutions institution "
    "JOIN external.ror_organizations organization ON organization.ror_id = institution.ror_id "
    "ON CONFLICT (institution_id) DO UPDATE SET ror_id = EXCLUDED.ror_id, match_method = 'openalex', "
    "match_score = NULL, run_id = EXCLUDED.run_id, matched_at = now()"
)
UPSERT_CROSSWALK_MATCH = (
    "INSERT INTO external.institution_crosswalk (institution_id, ror_id, match_method, match_score, run_id) "
    "VALUES (%(institution_id)s, %(ror_id)s, 'affiliation_string', %(match_score)s, %(run_id)s) "
    "ON CONFLICT (institution_id) DO UPDATE SET ror_id = EXCLUDED.ror_id, "
    "match_method = 'affiliation_string', match_score = EXCLUDED.match_score, run_id = EXCLUDED.run_id, "
    "matched_at = now()"
)

WANTED_ROR_IDS = "SELECT DISTINCT ror_id FROM core.institutions WHERE ror_id IS NOT NULL"
UNMATCHED_INSTITUTIONS = (
    "SELECT institution.institution_id, institution.display_name FROM core.institutions institution "
    "WHERE NOT EXISTS (SELECT 1 FROM external.institution_crosswalk crosswalk "
    "                  WHERE crosswalk.institution_id = institution.institution_id) "
    "ORDER BY institution.institution_id"
)


def wanted_ror_ids(conn: psycopg.Connection) -> set[str]:
    """The ROR ids OpenAlex already attached to institutions in the corpus."""
    return {row[0] for row in conn.execute(WANTED_ROR_IDS).fetchall()}


def unmatched_institutions(conn: psycopg.Connection, limit: int | None = None) -> list[tuple[str, str]]:
    """(institution_id, display_name) for institutions with no crosswalk row yet."""
    query = UNMATCHED_INSTITUTIONS + (f" LIMIT {int(limit)}" if limit else "")
    return [(row[0], row[1]) for row in conn.execute(query).fetchall()]


def load_ror_organizations(conn: psycopg.Connection, batch: Sequence[OrganizationRows], *, run_id: int) -> int:
    """Upsert organisations and replace their relationship rows."""
    if not batch:
        return 0
    ror_ids = [rows.organization["ror_id"] for rows in batch]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_ROR_ORGANIZATION, [{**rows.organization, "run_id": run_id} for rows in batch])
        cur.execute("DELETE FROM external.ror_relationships WHERE ror_id = ANY(%s)", (ror_ids,))
        cur.executemany(INSERT_ROR_RELATIONSHIP, [row for rows in batch for row in rows.relationships])
    return len(batch)


def link_crosswalk_from_openalex(conn: psycopg.Connection, *, run_id: int) -> int:
    """Link every institution whose OpenAlex ROR id is present in the loaded ROR subset."""
    with conn.cursor() as cur:
        cur.execute(INSERT_CROSSWALK_FROM_OPENALEX, {"run_id": run_id})
        return cur.rowcount


def link_crosswalk_match(
    conn: psycopg.Connection, institution_id: str, ror_id: str, score: float, *, run_id: int
) -> None:
    conn.execute(
        UPSERT_CROSSWALK_MATCH,
        {"institution_id": institution_id, "ror_id": ror_id, "match_score": score, "run_id": run_id},
    )


def load_country_profiles(conn: psycopg.Connection, profiles: Sequence[CountryProfile], *, run_id: int) -> int:
    """Upsert the country dimension (name included) and the World Bank profile rows."""
    if not profiles:
        return 0
    rows = [
        {
            "country_code": profile.country_code,
            "iso3_code": profile.iso3_code,
            "name": profile.name,
            "region": profile.region,
            "income_level": profile.income_level,
            "capital_city": profile.capital_city,
            "latitude": profile.latitude,
            "longitude": profile.longitude,
            "run_id": run_id,
        }
        for profile in profiles
    ]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_COUNTRY, rows)
        cur.executemany(UPSERT_COUNTRY_PROFILE, rows)
    return len(rows)


def load_indicator_catalogue(conn: psycopg.Connection, indicators: dict[str, str]) -> int:
    rows = [{"indicator_code": code, "name": name} for code, name in indicators.items()]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_INDICATOR, rows)
    return len(rows)


def load_observations(conn: psycopg.Connection, observations: Sequence[dict], *, run_id: int) -> int:
    """Store observations for countries we know; the API also reports codes outside core.countries."""
    if not observations:
        return 0
    known = {row[0] for row in conn.execute("SELECT country_code FROM core.countries").fetchall()}
    rows = [{**observation, "run_id": run_id} for observation in observations if observation["country_code"] in known]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_OBSERVATION, rows)
    return len(rows)


UPSERT_AFFILIATION_MATCH = (
    "INSERT INTO external.affiliation_matches (affiliation, ror_id, match_score, run_id) "
    "VALUES (%(affiliation)s, %(ror_id)s, %(match_score)s, %(run_id)s) "
    "ON CONFLICT (affiliation) DO UPDATE SET ror_id = EXCLUDED.ror_id, "
    "match_score = EXCLUDED.match_score, run_id = EXCLUDED.run_id, matched_at = now()"
)


def missing_ror_ids(conn: psycopg.Connection, ror_ids: Iterable[str]) -> set[str]:
    """Of `ror_ids`, the ones not yet in external.ror_organizations."""
    wanted = set(ror_ids)
    if not wanted:
        return set()
    present = conn.execute(
        "SELECT ror_id FROM external.ror_organizations WHERE ror_id = ANY(%s)", (sorted(wanted),)
    ).fetchall()
    return wanted - {row[0] for row in present}


def load_affiliation_matches(conn: psycopg.Connection, results: Sequence[tuple[str, object]], *, run_id: int) -> int:
    """Cache one row per affiliation string; a confident no-match is stored with a NULL ror_id."""
    rows = [
        {
            "affiliation": affiliation,
            "ror_id": getattr(match, "ror_id", None),
            "match_score": getattr(match, "score", None),
            "run_id": run_id,
        }
        for affiliation, match in results
    ]
    if rows:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_AFFILIATION_MATCH, rows)
    return len(rows)
```

- [ ] **Step 4: 确认通过**

Run: `uv run pytest tests/integration/test_external_loader.py -v`
Expected: `11 passed`

Run: `uv run pytest`
Expected: `93 passed`

- [ ] **Step 5: 提交**

```bash
git add src/scholarscope/external/loader.py tests/integration/test_external_loader.py
git commit -m "feat: idempotent writes for ROR and World Bank data" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: 两条富化流程

**Files:**
- Create: `src/scholarscope/external/pipeline.py`, `tests/integration/test_external_pipeline.py`
- Modify: `src/scholarscope/ingestion/runs.py`（`start_run` 增加 `source_id` 关键字参数）, `tests/support.py`（新增两个假 API）

**Interfaces:**
- Consumes: Task 2–5 的全部接口；`scholarscope.ingestion.runs.start_run/finish_run`
- Produces:
  - `runs.start_run(conn, profile, params, *, source_id: str = SOURCE_ID) -> int`——`source_id` 指向 `meta.data_sources`（`openalex` / `ror` / `worldbank`）
  - `BATCH_SIZE = 500`；`MATCH_PAUSE_S = 0.2`
  - `RorResult(run_id, version, organizations_loaded, institutions_linked, affiliations_matched, affiliations_unmatched)`；`WorldBankResult(run_id, countries, observations, missing_values)`
  - `load_dump_subset(conn, dump_path, wanted, *, run_id) -> int`
  - `match_unlinked_affiliations(conn, http, dump_path, *, run_id, min_score, limit, sleep) -> tuple[int, int, int]`
  - `match_institutions_without_ror(conn, http, dump_path, *, run_id, min_score, sleep) -> tuple[int, int]`
  - `enrich_ror(conn, http, *, dump_dir, dump_path=None, match_affiliations=True, min_score=0.8, max_affiliations=None, sleep=time.sleep) -> RorResult`
  - `enrich_worldbank(conn, http, *, from_year, to_year) -> WorldBankResult`
  - 出错时运行记为 `failed` 并重新抛出；`conn` 必须是 autocommit
  - 测试支持（`tests/support.py`）：`FakeRorApi(matches)`（`.requests`、`.http()`）、`FakeWorldBankApi(countries, indicator_values)`（`.fail_indicator`、`.http()`）、`world_bank_country(iso2, iso3, name, region=...)`

- [ ] **Step 1: 扩展运行记录并写测试替身**

在 `src/scholarscope/ingestion/runs.py` 中，把 `start_run` 整体替换为：

```python
def start_run(conn: psycopg.Connection, profile: str, params: dict, *, source_id: str = SOURCE_ID) -> int:
    """Open a run. `source_id` names the data source in meta.data_sources (openalex, ror, worldbank)."""
    row = conn.execute(
        "INSERT INTO meta.ingestion_runs (source_id, profile, params, status) "
        "VALUES (%s, %s, %s, 'running') RETURNING run_id",
        (source_id, profile, Jsonb(params)),
    ).fetchone()
    return row[0]
```

在 `tests/support.py` 末尾追加：

```python
class FakeRorApi:
    """ROR's affiliation endpoint: returns the mapping's ROR id for a known affiliation string."""

    def __init__(self, matches: dict[str, tuple[str, float]]) -> None:
        self.matches = matches
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        affiliation = request.url.params["affiliation"]
        self.requests.append(affiliation)
        found = self.matches.get(affiliation)
        if found is None:
            return httpx.Response(200, json={"items": []})
        ror_id, score = found
        return httpx.Response(200, json={"items": [{
            "score": score, "chosen": True, "matching_type": "SINGLE SEARCH",
            "organization": {"id": f"https://ror.org/{ror_id}"},
        }]})

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), base_url="https://api.ror.org")


class FakeWorldBankApi:
    """World Bank v2: serves the country list and one payload per indicator code."""

    def __init__(self, countries: list, indicator_values: dict[str, list[tuple[str, int, float | None]]]) -> None:
        self.countries = countries
        self.indicator_values = indicator_values
        self.requests: list[str] = []
        self.fail_indicator: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        envelope = {"page": 1, "pages": 1, "per_page": 20000, "total": 0}
        if path.endswith("/country"):
            return httpx.Response(200, json=[{**envelope, "total": len(self.countries)}, self.countries])
        code = path.rsplit("/", 1)[-1]
        if code == self.fail_indicator:
            return httpx.Response(200, json=[{"message": [{"id": "120", "key": "Invalid value", "value": code}]}])
        rows = [
            {"indicator": {"id": code}, "country": {"id": country}, "countryiso3code": "",
             "date": str(year), "value": value}
            for country, year, value in self.indicator_values.get(code, [])
        ]
        return httpx.Response(200, json=[{**envelope, "total": len(rows)}, rows])

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), base_url="https://api.worldbank.org")


def world_bank_country(iso2: str, iso3: str, name: str, region: str = "East Asia & Pacific") -> dict:
    return {
        "id": iso3, "iso2Code": iso2, "name": name,
        "region": {"id": "EAS", "iso2code": "Z4", "value": region},
        "incomeLevel": {"id": "HIC", "iso2code": "XD", "value": "High income"},
        "capitalCity": "Capital", "longitude": "1.5", "latitude": "2.5",
    }
```

- [ ] **Step 2: 写失败测试**

创建 `tests/integration/test_external_pipeline.py`：

```python
from pathlib import Path

import pytest

from scholarscope.external import pipeline
from scholarscope.external.worldbank import INDICATORS, WorldBankError
from scholarscope.ingestion import runs
from scholarscope.ingestion.loader import load_works
from scholarscope.ingestion.transform import transform_work
from tests.support import FakeRorApi, FakeWorldBankApi, world_bank_country

pytestmark = pytest.mark.db

DUMP = Path(__file__).parents[1] / "fixtures" / "ror" / "ror_sample.zip"
NTU = "Nanyang Technological University, Singapore"


@pytest.fixture
def corpus(db, works_page):
    """Three works, and one authorship left unlinked by OpenAlex but carrying a raw affiliation."""
    preprint = next(w for w in works_page["results"] if w["id"].endswith("W4389984066"))
    preprint["authorships"][0]["raw_affiliation_strings"] = [NTU]
    run_id = runs.start_run(db, "test", {})
    with db.transaction():
        load_works(db, [transform_work(w) for w in works_page["results"]], run_id=run_id, query_key="rag")
    return run_id


def scalar(db, query: str):
    return db.execute(query).fetchone()[0]


def test_enrich_ror_loads_the_subset_links_and_matches(db, corpus, tmp_path):
    api = FakeRorApi({NTU: ("02e7b5302", 1.0)})

    result = pipeline.enrich_ror(
        db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None
    )

    assert result.version == "ror_sample.zip"  # a local dump keeps its file name as the version
    assert result.institutions_linked == 3  # Google, Harbin IT, Huawei came with ROR ids
    assert (result.affiliations_matched, result.affiliations_unmatched) == (1, 0)
    assert result.organizations_loaded == 4  # three from the corpus plus NTU, pulled in by the match
    assert scalar(db, "SELECT count(*) FROM external.institution_crosswalk") == 3
    assert scalar(db, "SELECT count(*) FROM external.ror_organizations WHERE ror_id = '02e7b5302'") == 1
    assert scalar(db, "SELECT count(*) FROM bridge.authorship_ror_institutions") == 1
    assert ("W4389984066", "SG") in db.execute("SELECT work_id, country_code FROM bridge.work_countries").fetchall()
    assert api.requests == [NTU]


def test_enrich_ror_records_the_release_in_the_run(db, corpus, tmp_path):
    result = pipeline.enrich_ror(
        db, FakeRorApi({}).http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None
    )

    row = db.execute(
        "SELECT source_id, profile, status, params FROM meta.ingestion_runs WHERE run_id = %s", (result.run_id,)
    ).fetchone()
    assert row[:3] == ("ror", "dump", "succeeded")
    assert row[3]["file_name"] == "ror_sample.zip"
    assert len(row[3]["sha256"]) == 64
    assert row[3]["min_score"] == pytest.approx(0.8)


def test_enrich_ror_is_idempotent_and_reuses_the_match_cache(db, corpus, tmp_path):
    api = FakeRorApi({NTU: ("02e7b5302", 1.0)})
    first = pipeline.enrich_ror(db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None)

    second = pipeline.enrich_ror(db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None)

    assert second.institutions_linked == first.institutions_linked
    assert second.affiliations_matched == 0  # the string is cached, so ROR is not asked again
    assert api.requests == [NTU]
    assert scalar(db, "SELECT count(*) FROM external.ror_organizations") == 4
    assert scalar(db, "SELECT count(*) FROM external.affiliation_matches") == 1


def test_enrich_ror_caches_a_confident_no_match(db, corpus, tmp_path):
    result = pipeline.enrich_ror(
        db, FakeRorApi({}).http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None
    )

    assert (result.affiliations_matched, result.affiliations_unmatched) == (0, 1)
    assert db.execute(
        "SELECT ror_id, match_score FROM external.affiliation_matches WHERE affiliation = %s", (NTU,)
    ).fetchone() == (None, None)
    assert scalar(db, "SELECT count(*) FROM bridge.authorship_ror_institutions") == 0


def test_enrich_ror_can_skip_affiliation_matching(db, corpus, tmp_path):
    api = FakeRorApi({NTU: ("02e7b5302", 1.0)})

    result = pipeline.enrich_ror(
        db, api.http(), dump_dir=tmp_path, dump_path=DUMP, match_affiliations=False, sleep=lambda _s: None
    )

    assert (result.affiliations_matched, result.affiliations_unmatched) == (0, 0)
    assert api.requests == []
    assert scalar(db, "SELECT count(*) FROM external.affiliation_matches") == 0


def test_enrich_ror_matches_an_institution_openalex_left_without_a_ror_id(db, corpus, tmp_path):
    db.execute("INSERT INTO core.institutions (institution_id, display_name, ror_id) VALUES ('I99', 'NTU', NULL)")
    api = FakeRorApi({"NTU": ("02e7b5302", 0.91)})

    result = pipeline.enrich_ror(
        db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None
    )

    assert result.institutions_linked == 4  # the three OpenAlex ids plus the name-matched one
    row = db.execute(
        "SELECT ror_id, match_method, match_score FROM external.institution_crosswalk WHERE institution_id = 'I99'"
    ).fetchone()
    assert row[:2] == ("02e7b5302", "affiliation_string")
    assert row[2] == pytest.approx(0.91)


def test_failing_ror_run_is_marked_failed_and_reraised(db, corpus, tmp_path):
    from scholarscope.external.ror import RorDumpError

    empty = tmp_path / "empty.zip"
    import zipfile

    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "not a dump")

    with pytest.raises(RorDumpError):
        pipeline.enrich_ror(db, FakeRorApi({}).http(), dump_dir=tmp_path, dump_path=empty, sleep=lambda _s: None)

    assert scalar(db, "SELECT status FROM meta.ingestion_runs ORDER BY run_id DESC LIMIT 1") == "failed"


def world_bank_api() -> FakeWorldBankApi:
    countries = [
        world_bank_country("SG", "SGP", "Singapore"),
        world_bank_country("CN", "CHN", "China"),
        {"id": "AFE", "iso2Code": "ZH", "name": "Africa Eastern and Southern",
         "region": {"id": "NA", "iso2code": "", "value": "Aggregates"},
         "incomeLevel": {"id": "NA", "value": "Aggregates"}, "capitalCity": "", "longitude": "", "latitude": ""},
    ]
    values = {code: [("SG", 2024, 1.0), ("CN", 2024, 2.0), ("SG", 2026, None)] for code in INDICATORS}
    return FakeWorldBankApi(countries, values)


def test_enrich_worldbank_stores_countries_indicators_and_nulls(db, corpus):
    api = world_bank_api()

    result = pipeline.enrich_worldbank(db, api.http(), from_year=2019, to_year=2026)

    assert result.countries == 2  # the aggregate row is excluded
    assert result.observations == len(INDICATORS) * 3
    assert result.missing_values == len(INDICATORS)  # one NULL per indicator
    assert scalar(db, "SELECT name FROM core.countries WHERE country_code = 'SG'") == "Singapore"
    assert scalar(db, "SELECT count(*) FROM external.indicators") == len(INDICATORS)
    assert db.execute(
        "SELECT value FROM external.country_indicators "
        "WHERE country_code = 'SG' AND indicator_code = 'SP.POP.TOTL' AND year = 2026"
    ).fetchone() == (None,)
    assert scalar(db, "SELECT status FROM meta.ingestion_runs WHERE run_id = %s" % result.run_id) == "succeeded"
    assert scalar(db, "SELECT source_id FROM meta.ingestion_runs WHERE run_id = %s" % result.run_id) == "worldbank"


def test_enrich_worldbank_is_idempotent(db, corpus):
    first = pipeline.enrich_worldbank(db, world_bank_api().http(), from_year=2019, to_year=2026)
    before = scalar(db, "SELECT count(*) FROM external.country_indicators")

    second = pipeline.enrich_worldbank(db, world_bank_api().http(), from_year=2019, to_year=2026)

    assert scalar(db, "SELECT count(*) FROM external.country_indicators") == before
    assert second.observations == first.observations
    assert scalar(
        db, "SELECT count(DISTINCT run_id) FROM external.country_indicators"
    ) == 1  # every row now points at the newer run


def test_failing_worldbank_run_is_marked_failed_and_reraised(db, corpus):
    api = world_bank_api()
    api.fail_indicator = "SP.POP.TOTL"

    with pytest.raises(WorldBankError):
        pipeline.enrich_worldbank(db, api.http(), from_year=2019, to_year=2026)

    assert scalar(db, "SELECT status FROM meta.ingestion_runs ORDER BY run_id DESC LIMIT 1") == "failed"
```

- [ ] **Step 3: 确认失败**

Run: `uv run pytest tests/integration/test_external_pipeline.py -v`
Expected: `ImportError: cannot import name 'pipeline' from 'scholarscope.external'`

- [ ] **Step 4: 实现**

创建 `src/scholarscope/external/pipeline.py`：

```python
"""Enrichment flows: ROR organisations and affiliations, and World Bank country indicators.

Both record a run in `meta.ingestion_runs` exactly as OpenAlex ingestion does, so every external row
can be traced to the release or API call that produced it. `conn` must be in autocommit mode: each
unit of work is its own transaction.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
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
    twice. Organisations the match points at are loaded from the dump before the cache references
    them, because the cache has a foreign key to `external.ror_organizations`.
    """
    affiliations = [row[0] for row in conn.execute(DISTINCT_UNLINKED_AFFILIATIONS).fetchall()]
    if limit is not None:
        affiliations = affiliations[:limit]
    if not affiliations:
        return 0, 0, 0

    results: list[tuple[str, AffiliationMatch | None]] = []
    for index, affiliation in enumerate(affiliations, start=1):
        results.append((affiliation, match_affiliation(http, affiliation, min_score=min_score)))
        if index % 100 == 0:
            log.info("run %s: asked ROR about %d of %d affiliation strings", run_id, index, len(affiliations))
        if index < len(affiliations):
            sleep(MATCH_PAUSE_S)

    referenced = {match.ror_id for _, match in results if match}
    added = load_dump_subset(conn, dump_path, loader.missing_ror_ids(conn, referenced), run_id=run_id)
    with conn.transaction():
        stored = loader.load_affiliation_matches(conn, results, run_id=run_id)
    matched = sum(1 for _, match in results if match)
    log.info("run %s: %d of %d affiliation strings matched (%d organisations added)",
             run_id, matched, stored, added)
    return matched, len(results) - matched, added


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
                stored = loader.load_observations(conn, series, run_id=run_id)
            observations += stored
            missing += sum(1 for observation in series if observation["value"] is None)
            log.info("World Bank run %s: %s -> %d observations stored", run_id, indicator_code, stored)
    except Exception as exc:
        runs.finish_run(conn, run_id, "failed", repr(exc))
        raise

    runs.finish_run(conn, run_id, "succeeded")
    return WorldBankResult(run_id, countries, observations, missing)
```

- [ ] **Step 5: 确认通过**

Run: `uv run pytest tests/integration/test_external_pipeline.py -v`
Expected: `10 passed`

Run: `uv run pytest`
Expected: `103 passed`

- [ ] **Step 6: 提交**

```bash
git add src/scholarscope/external/pipeline.py src/scholarscope/ingestion/runs.py tests/support.py tests/integration/test_external_pipeline.py
git commit -m "feat: ROR and World Bank enrichment pipelines" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 覆盖率指标、命令行与文档

**Files:**
- Modify: `src/scholarscope/quality/checks.py`（`CHECKS` 末尾新增四项）, `src/scholarscope/cli.py`（新增两个命令）, `docs/development.md`（新增"External data"一节与两行命令）, `tests/integration/test_cli_end_to_end.py`（新增三个测试）

**Interfaces:**
- Consumes: Task 6 的 `enrich_ror` / `enrich_worldbank`；Task 1 的视图
- Produces:
  - 新指标（全部为只记录的 metric，`threshold=None`）：`institutions_without_ror`、`corpus_countries_without_profile`、`works_without_country`、`rd_indicator_missing`
  - 命令 `scholarscope ror [--dump PATH] [--no-match] [--min-score FLOAT] [--max-affiliations N]`
  - 命令 `scholarscope worldbank [--from-year 2019] [--to-year YEAR]`（默认当前年）
  - 两个命令失败时打印一行到 stderr 并返回退出码 3；成功返回 0
  - `_plain_http(http)`：给外部 API 用的 httpx 客户端（各模块使用绝对 URL，需 `follow_redirects=True` 以跟随 Zenodo 的下载跳转）

- [ ] **Step 1: 写失败测试**

在 `tests/integration/test_cli_end_to_end.py` 顶部的导入改为：

```python
from tests.support import (
    TWO_QUERY_RECALL,
    TWO_QUERY_RECALL_TOML,
    FakeOpenAlex,
    FakeRorApi,
    FakeWorldBankApi,
    world_bank_country,
)
```

并在文件末尾追加：

```python
ROR_DUMP = Path(__file__).parents[1] / "fixtures" / "ror" / "ror_sample.zip"


def test_ror_command_enriches_and_reports(db, works_page, cli_settings, capsys):
    from scholarscope.ingestion.loader import load_works
    from scholarscope.ingestion.transform import transform_work
    from scholarscope.ingestion import runs as ingestion_runs

    run_id = ingestion_runs.start_run(db, "test", {})
    with db.transaction():
        load_works(db, [transform_work(w) for w in works_page["results"]], run_id=run_id, query_key="rag")
    api = FakeRorApi({})

    exit_code = main(
        ["ror", "--dump", str(ROR_DUMP), "--no-match"], settings=cli_settings, http=api.http()
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "3 institutions linked" in out
    assert db.execute("SELECT count(*) FROM external.institution_crosswalk").fetchone() == (3,)


def test_worldbank_command_enriches_and_reports(db, cli_settings, capsys):
    api = FakeWorldBankApi(
        [world_bank_country("SG", "SGP", "Singapore")],
        {code: [("SG", 2024, 1.0)] for code in INDICATORS},
    )

    exit_code = main(["worldbank", "--from-year", "2019", "--to-year", "2026"], settings=cli_settings, http=api.http())

    assert exit_code == 0
    assert "1 countries" in capsys.readouterr().out
    assert db.execute("SELECT count(*) FROM external.country_profiles").fetchone() == (1,)


def test_worldbank_command_exits_3_on_api_error(db, cli_settings, capsys):
    api = FakeWorldBankApi([world_bank_country("SG", "SGP", "Singapore")], {})
    api.fail_indicator = "SP.POP.TOTL"

    exit_code = main(["worldbank"], settings=cli_settings, http=api.http())

    assert exit_code == 3
    assert "World Bank enrichment failed" in capsys.readouterr().err
    assert db.execute(
        "SELECT status FROM meta.ingestion_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone() == ("failed",)
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/integration/test_cli_end_to_end.py -v`
Expected: 3 个新测试 FAIL，报 `argument command: invalid choice: 'ror'`

- [ ] **Step 3: 新增四项质量指标**

在 `src/scholarscope/quality/checks.py` 的 `CHECKS` 元组末尾（`references_outside_corpus` 那一项之后、`)` 之前）插入：

```python
    QualityCheck(
        "institutions_without_ror",
        "Institutions with no ROR id in the crosswalk (Plan 2 enrichment)",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM external.institution_crosswalk c WHERE c.institution_id = i.institution_id)), "
        "count(*) FROM core.institutions i",
        None,
    ),
    QualityCheck(
        "corpus_countries_without_profile",
        "Countries appearing in the corpus that the World Bank does not list (e.g. Taiwan)",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM external.country_profiles p WHERE p.country_code = c.country_code)), count(*) "
        "FROM (SELECT DISTINCT country_code FROM bridge.work_countries) c",
        None,
    ),
    QualityCheck(
        "works_without_country",
        "Works with no country attribution, after ROR affiliation recovery",
        "SELECT count(*) FILTER (WHERE NOT EXISTS "
        "(SELECT 1 FROM bridge.work_countries wc WHERE wc.work_id = w.work_id)), count(*) FROM core.works w",
        None,
    ),
    QualityCheck(
        "rd_indicator_missing",
        "Country-year cells with no R&D spending value (the World Bank reporting lag)",
        "SELECT count(*) FILTER (WHERE value IS NULL), count(*) FROM external.country_indicators "
        "WHERE indicator_code = 'GB.XPD.RSDV.GD.ZS'",
        None,
    ),
```

- [ ] **Step 4: 新增两个命令**

在 `src/scholarscope/cli.py` 中做四处修改。第一，导入区加入：

```python
from scholarscope.external import pipeline as external_pipeline
from scholarscope.external.ror import RorDumpError
from scholarscope.external.ror_match import DEFAULT_MIN_SCORE
from scholarscope.external.worldbank import WorldBankError
```

第二，在 `build_parser()` 里 `p_quality = sub.add_parser("quality", …)` 那一行之前插入：

```python
    p_ror = sub.add_parser("ror", help="load the corpus's ROR organisations and match loose affiliations")
    p_ror.add_argument("--dump", type=Path, help="use this ROR dump instead of downloading the newest release")
    p_ror.add_argument("--no-match", action="store_true", help="skip ROR affiliation matching entirely")
    p_ror.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE, help="lowest accepted match score")
    p_ror.add_argument("--max-affiliations", type=int, help="stop after this many affiliation strings")

    p_worldbank = sub.add_parser("worldbank", help="refresh World Bank country profiles and indicators")
    p_worldbank.add_argument("--from-year", type=int, default=2019)
    p_worldbank.add_argument("--to-year", type=int, help="defaults to the current year")
```

第三，在 `def _client(settings, http)` 之前插入：

```python
def _plain_http(http: httpx.Client | None) -> httpx.Client:
    """A client for the external APIs (ROR, Zenodo, World Bank); each module uses absolute URLs."""
    if http is not None:
        return http
    return httpx.Client(timeout=httpx.Timeout(120.0), headers={"User-Agent": USER_AGENT}, follow_redirects=True)
```

第四，在 `main()` 里 `if args.command == "quality":` 那一行之前插入：

```python
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
```

- [ ] **Step 5: 确认通过**

Run: `uv run pytest tests/integration/test_cli_end_to_end.py -v`
Expected: 全部通过（含 3 个新测试）

Run: `uv run pytest`
Expected: `106 passed`

```bash
uv run scholarscope ror --help
uv run scholarscope worldbank --help
```

Expected：两条命令的参数与上面 Interfaces 一致。

- [ ] **Step 6: 更新开发文档**

用下面内容整体替换 `docs/development.md`：

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
`--resume RUN_ID` once the underlying problem is fixed); `quality` returns 1 when any gate fails.

## External data (Plan 2)

`ror` downloads the newest ROR release from Zenodo (~37 MB, CC0) into `data/raw/ror/`, loads only the
organisations the corpus references, and fills `external.institution_crosswalk`. It then asks ROR's
affiliation endpoint about institutions OpenAlex left without a ROR id and about raw affiliation
strings on authorships it never linked; each distinct string is asked once and cached in
`external.affiliation_matches`, including confident no-matches. Use `--dump PATH` to load a dump you
already have, `--no-match` to skip the API step, and `--min-score` to change the 0.8 acceptance
threshold.

`worldbank` refreshes `external.country_profiles` and six indicator series into
`external.country_indicators`. Coverage is uneven by design of the source: population is complete,
GDP nearly so, but R&D spending and researchers-per-million cover ~40% of countries and stop around
2023, and Taiwan is absent from the World Bank entirely. Values are stored as they come, NULLs
included; `scholarscope quality` reports the gaps.

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

- [ ] **Step 7: 提交**

```bash
git add src/scholarscope/quality/checks.py src/scholarscope/cli.py docs/development.md tests/integration/test_cli_end_to_end.py
git commit -m "feat: external coverage metrics, ror/worldbank commands and docs" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: 真实验收与架构文档同步

**Files:**
- Modify: `任务架构.md`（§4.2、§4.3、§5.3 实际采集结果、§19 事实面状态）

**Interfaces:**
- Consumes: Task 7 的命令
- Produces: 项目数据库里的 ROR 与 World Bank 数据；与实际运行一致的架构文档

本任务会真实调用 Zenodo、ROR 与 World Bank；三者都免费。参考值取自 2026-09-23 对语料副本的验收，会随数据源更新缓慢变化。

- [ ] **Step 1: 确认起点**

```bash
docker compose up -d --wait
uv run scholarscope migrate
uv run pytest -q
```

Expected：`0003 (head)` 已应用；`106 passed`。

- [ ] **Step 2: ROR 富化**

Run: `uv run scholarscope ror`
Expected：先从 Zenodo 下载约 37.5 MB 转储到 `data/raw/ror/`，随后两遍扫描 CSV、逐个匹配约 800 个字符串，总耗时约 9 分 11 秒（其中约 8 分钟是 796 次匹配调用，两遍 CSV 扫描各约 13 秒）。输出形如：

```text
run 4: ROR v2.13-2026-09-22, 5747 organisations, 5683 institutions linked, 155 affiliation strings matched (641 unmatched)
```

- [ ] **Step 3: World Bank 富化**

Run: `uv run scholarscope worldbank`
Expected：约 10 秒，输出形如：

```text
run 5: 217 countries, 9114 observations (3445 without a value), 2019-2026
```

- [ ] **Step 4: 质量指标**

Run: `uv run scholarscope quality`
Expected：4 个质量门仍然全部 `PASS`；新增四项指标的参考值：

| 指标 | 实测值 | 说明 |
|---|---:|---|
| `institutions_without_ror` | 1 / 5,684（0.0%） | 富化前是 17 家 |
| `corpus_countries_without_profile` | 1 / 138（0.7%） | 就是台湾 |
| `works_without_country` | 6,586 / 15,921（41.4%） | 富化前 6,672 篇 |
| `rd_indicator_missing` | 1,048 / 1,519（69.0%） | World Bank 的研发数据本身就缺这么多 |

- [ ] **Step 5: 用 SQL 抽查**

```bash
docker compose exec postgres psql -U scholarscope -d scholarscope -c "SELECT o.country_name, count(*) AS institutions FROM external.institution_crosswalk c JOIN external.ror_organizations o USING (ror_id) GROUP BY 1 ORDER BY 2 DESC LIMIT 8"
docker compose exec postgres psql -U scholarscope -d scholarscope -c "SELECT relationship_type, count(*) FROM bridge.institution_relationships GROUP BY 1 ORDER BY 2 DESC"
docker compose exec postgres psql -U scholarscope -d scholarscope -c "SELECT p.name, i.value FROM external.country_indicators i JOIN external.country_profiles p USING (country_code) WHERE i.indicator_code = 'GB.XPD.RSDV.GD.ZS' AND i.year = 2023 AND i.value IS NOT NULL ORDER BY i.value DESC LIMIT 5"
docker compose exec postgres psql -U scholarscope -d scholarscope -c "SELECT count(DISTINCT work_id) AS works_with_country FROM bridge.work_countries"
```

Expected：机构最多的国家与语料国家分布一致（中国、美国、印度居前）；关系类型以 parent/child 为主；研发投入最高的国家为以色列、韩国等；带国家的论文数约 9,335。

- [ ] **Step 6: 重跑一次，确认幂等**

```bash
uv run scholarscope ror --dump data/raw/ror/<刚下载的文件名>
uv run scholarscope worldbank
```

Expected：ROR 第二次运行 `0 affiliation strings matched`（都命中缓存，不再调用 API）；两次运行后 `external.ror_organizations`、`external.institution_crosswalk`、`external.country_indicators` 的行数不变。

- [ ] **Step 7: 同步 `任务架构.md`**

1. §4.2 ROR 一节：把用途列表之后补一句实际做法——"通过 Zenodo 上的版本化转储（CC0）加载语料引用到的机构，记录版本号与 SHA-256；OpenAlex 未给 ROR id 的机构和未关联的原始单位字符串，再用 ROR affiliation 接口按 0.8 分阈值匹配"，并写明本次使用的版本与匹配结果。
2. §4.3 World Bank 一节：把候选指标列表改为实际采集的六个指标代码，并写明覆盖率实测（人口完整、GDP 约 96%、研发类约 40% 且止于 2023）与台湾不在列表中的事实。
3. §5.3"实际采集结果"后追加一行：ROR 与 World Bank 两次运行的编号、结果与耗时（来自 Step 2、Step 3 的输出）。
4. §19"当前事实面状态"表：“代码”一行改为 `Plan 1 数据基座 + Plan 2 外部数据融合：106 项自动化测试通过；ROR 与 World Bank 真实富化成功`。
5. §19"必须通过数据探查决定"里的"是否加入 Crossref"补注：语料 1,715 篇（10.8%）无 DOI，Crossref 帮助有限，推迟到 Plan 3 分析层暴露具体缺口后再定。

- [ ] **Step 8: 提交**

```bash
git add 任务架构.md
git commit -m "docs: record Plan 2 enrichment results in the architecture" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

之后使用 superpowers:finishing-a-development-branch 决定合并方式。

---

## 完成标准

- `uv run pytest` 显示 `106 passed`；`uv run pytest -m "not db"` 显示 `55 passed, 51 deselected`。
- `uv run scholarscope ror` 与 `uv run scholarscope worldbank` 真实运行成功，重跑不产生重复行、不重复调用 API。
- `scholarscope quality` 的 4 个质量门仍全部 PASS，新增四项指标有真实数值。
- `任务架构.md` 记录了两个数据源的实际做法、覆盖率与已知缺口（台湾、研发指标滞后）。
- `git log` 中每个任务一个提交，`data/raw/` 不在版本库中。

## 本计划完成后需要团队决定的事项

1. **研发类指标的用法**：数据止于 2023，建议分析层统一取"截至第 T 年的最近可得值"并在图注写明年份；需要团队确认这个口径。
2. **台湾等缺口的呈现方式**：156 篇台湾论文没有 World Bank 指标，图表里是单列、归入"无指标"还是脚注说明。
3. **未匹配的原始单位字符串**：约 641 个字符串 ROR 给不出可信匹配，是否需要人工整理一批规则。
4. **下一份计划**：建议 Plan 3（分析层），数据已经齐备，能最快产出 PPT 图表。
