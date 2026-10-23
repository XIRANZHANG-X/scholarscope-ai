# ScholarScope · RAG 研究，2019–2026

[English](README.md) · **简体中文**

一个可复现的 **RAG（检索增强生成）研究**文献计量项目。数据是 OpenAlex 上标题或摘要提到
"retrieval augmented generation" 的全部计算机领域论文（15,921 篇，2019-01-01 至 2026-09-22），
再用 ROR 补全机构信息、用 World Bank 补充国家指标，全部装进 PostgreSQL，然后用保存好的 SQL 回答五个问题：

| # | 问题 | 主要发现 |
|---|---|---|
| 1 | RAG 增长有多快？和 LLM、AI Agents、Foundation models 比呢？ | RAG 从 2023 年的 133 篇到 2024 年的 1,966 篇，一年 14.8 倍，是四个主题里最陡的单年增长。 |
| 2 | 发在哪里？各类型被引情况如何？ | 约一半是预印本（2024 年 52%）；预印本被引最少：2024 年的预印本 55% 被引过，会议论文是 88%。 |
| 3 | 哪些国家在做？谁和谁合作？ | 在有国家信息的论文里，中国（2,494）和美国（2,306）合占一半；中美是最大的合作组合（301 篇）。 |
| 4 | 研究什么？什么在升温？ | Agents 的占比从 11% 升到 24%（2024 → 2026）；微调从 21% 降到 13%。 |
| 5 | 国家的富裕程度和研发投入能预测产出吗？ | 人均论文数与人均 GDP 大致 1:1 同比例增长（负二项回归弹性 1.07）；控制了财富之后，研发占 GDP 比例没有额外作用。 |

图和网页上的每个数都来自对数据库的 SQL 查询；CSV 只是查询结果的副本。

**产出**

- 35 张图（`analysis/*/output/`、`country_indicators_analysis/output/`）：300 dpi PNG（4000×2250）和 PDF，
  每张图旁边都有它所画数据的 CSV。
- 一个单文件网页（`site/dist/scholarscope.html`，约 6 MB），包含 35 张图的可交互版本。
  双击就能离线打开，不需要服务器，也不需要联网。

---

## 复现步骤

**需要：** [Docker Desktop](https://www.docker.com/products/docker-desktop/)、
[uv](https://docs.astral.sh/uv/)（会自动装好 Python 3.13）、约 2 GB 磁盘空间。

```bash
git clone https://github.com/XIRANZHANG-X/scholarscope-ai.git
cd scholarscope-ai
cp .env.example .env               # 设置 POSTGRES_PASSWORD
uv sync
docker compose up -d --wait        # PostgreSQL 17，地址 127.0.0.1:5432
```

连接地址请写 `127.0.0.1`，不要写 `localhost`：Windows 上 `localhost` 会先解析成 `::1`，连接会卡住。

### 第 1 步 · 准备数据（二选一）

**A. 恢复已发布的快照（精确复现，约 1 分钟）。** 从
[`data-2026-09-22` Release](https://github.com/XIRANZHANG-X/scholarscope-ai/releases/tag/data-2026-09-22)
下载 `scholarscope.dump`（12 MB），然后：

```bash
docker compose exec -T postgres pg_restore -U scholarscope -d scholarscope --clean --if-exists --no-owner < scholarscope.dump
```

**B. 自己从 API 采集（约 20 分钟）。** 在 `.env` 里填一个免费的 OpenAlex API key
（<https://openalex.org/settings/api>）。全量采集约花 0.16 美元，在每天 1 美元的免费额度内。

```bash
uv run scholarscope migrate
uv run scholarscope ingest --profile standard
uv run scholarscope ror
uv run scholarscope worldbank
uv run scholarscope quality
```

OpenAlex 一直在更新，所以路线 B 拿到的是当天的数据，和本仓库里的图会略有出入；要精确复现请用路线 A。
各命令的参数、退出码和费用见 [`docs/development.md`](docs/development.md)。

两条路线都要再补一次台湾（World Bank 没有台湾的数据，重复执行无害）：

```bash
docker compose exec -T postgres psql -U scholarscope -d scholarscope < country_indicators_analysis/sql/00_add_taiwan.sql
```

### 第 2 步 · 查询 → 出图

```bash
uv run python analysis/run_all.py --strict
```

依次运行五个分析脚本：每个脚本用只读连接执行自己 `sql/` 下的查询，结果存成 `output/*.csv`，
再画成 `output/*.png` 和 `*.pdf`。`--strict` 表示连不上数据库就报错，不会悄悄改用 CSV；
加 `--from-csv` 则不连数据库，直接用仓库里的 CSV 重画。

### 第 3 步 · 网页

```bash
uv run python site/build_site.py
```

执行同一批查询，把数据和 plotly.js 一起写进 `site/dist/scholarscope.html`。
网页是查询结果的快照，数据变了就重新生成一次。

### 检查

```bash
uv run pytest
```

完整流程的中文说明见 [`docs/流程.md`](docs/流程.md)。

---

## 仓库结构

```
src/scholarscope/        数据采集：OpenAlex 采集、ROR 与 World Bank 补全、质量检查（命令行 `scholarscope`）
sql/migrations/          数据库表结构（Alembic）：meta、core、bridge、external
compose.yaml             PostgreSQL 17（另有可选的 pgAdmin：`docker compose --profile ui up -d`）
analysis/
  common.py              统一的图表样式和只读查询函数
  run_all.py             一次重新生成五个方向
  01_theme_comparison/   RAG 与 LLM、Agents、Foundation models 对比
  02_publication_types/  预印本、会议论文、期刊文章；被引；发表载体
  03_world_collaboration/ 国家分布、国际合作、地图
  04_subfields/          用关键词规则划分的 RAG 子方向（subfield_rules.csv）
country_indicators_analysis/  国家财富、研发投入、人口与产出（负二项回归）
site/                    单文件网页：build_site.py、charts.py、页面模板与样式
scripts/export_csv.py    把数据库导出为 CSV
docs/                    数据字典、开发说明、完整流程
tests/                   单元测试与集成测试
```

每个分析目录结构相同：`sql/`（一张图一条查询）、`output/`（查询结果 CSV 和图）、
`make_figures.py`（一张图一个函数）、`README.md`（逐图说明：画的是什么、每列的含义、口径与数据坑）。
图表样式规范见 [`analysis/图表统一规范.md`](analysis/图表统一规范.md)，
字段含义见 [`docs/数据字典.md`](docs/数据字典.md)。

---

## 数据坑

引用任何数字之前请先看这几条：

1. **15.2% 的论文日期是占位的 1 月 1 日**：OpenAlex 只知道年份。按季度统计时已剔除。
2. **41.4% 的论文没有作者国家**，大部分是没有单位信息的预印本。国别结论只描述其余部分。
3. **一篇多国论文在每个国家各算一次**，所以各国合计会超过论文总数。
4. **2026 年只到 9 月 22 日**，当年的会议论文还在陆续收录。图中写作 2026*。
5. **LLM、Agents、Foundation models 的数字是检索命中数**（几个短语相加，是上限），数据库里只有 RAG 论文本身。
6. **子方向是关键词规则**：匹配上表示「提到」，不等于「研究的就是它」。只看标题的口径更严格。
7. **发表类型是 OpenAlex 的标签**，有噪声（例如 Zenodo 上传的文件被标成期刊文章）。

## 数据来源与许可

| 来源 | 用途 | 许可 |
|---|---|---|
| [OpenAlex](https://openalex.org) | 论文、作者、机构、引用 | CC0 |
| [ROR](https://ror.org) | 机构名称、国家、经纬度 | CC0 |
| [World Bank Open Data](https://data.worldbank.org) | 人均 GDP、研发占比、人口 | CC BY 4.0 |
| [Natural Earth](https://www.naturalearthdata.com) | 地图轮廓 | 公有领域 |
| 台湾指标（主计总处、内政部、国科会） | 手工补入，见 `country_indicators_analysis/README.md` | 政府开放数据 |

代码采用 [MIT 许可证](LICENSE)，数据沿用上表各自的许可。
