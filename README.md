# ScholarScope AI

NTU 课程项目。我们手上有一份关于 **RAG（检索增强生成）** 这个研究方向的完整数据：
它怎么从 2019 年的 4 篇论文长到 2026 年的一万多篇，谁在做、在哪做、和各国的科研投入有没有关系。

**数据已经采集、清洗、标准化完毕，可以直接开始分析。**
用什么工具、做哪些分析、讲什么故事——都还没定，欢迎在 issue 里讨论。

截止日期：**2026-10-23**。原始设计文档见 [`任务架构.md`](任务架构.md)（内容较多，不读也不影响动手）。

---

## 一、我们有什么数据

三个公开数据源融合而成，全部是**全量**而非抽样：

| | 数量 | 说明 |
|---|---:|---|
| **论文** | **15,921 篇** | 标题或摘要含 "retrieval augmented generation"，计算机领域，2019-01-01 至 2026-09-22 |
| 作者 | 48,101 位 | 68,797 条「某篇论文的第 N 位作者」记录 |
| 机构 | 5,684 家 | 已用 ROR 标准化，带国家、城市、**经纬度** |
| 引用 | 104,227 条 | 其中 9,446 条是语料内部的引用 |
| 主题标签 | 45,322 条 | OpenAlex 的主题分类，带置信分 |
| 关键词 | 108,364 条 | 同上 |
| 国家指标 | 217 国 × 6 指标 × 7 年 | World Bank：人口、GDP、人均 GDP、研发投入占比、每百万研究人员、高技术出口 |
| 宏观背景 | 4 主题 × 8 年 | RAG / LLM / Agents / Foundation Models 的年度论文总数对比 |

**每个字段的具体含义见 [`docs/数据字典.md`](docs/数据字典.md)** —— 包含示例值、覆盖率，
以及 8 个必须知道的数据坑（比如有 15.2% 的论文日期是假的）。

---

## 二、怎么拿到数据

### 路线 A：只要数据，不碰 Docker 和数据库 ← 推荐给用 Tableau / Excel / R 的同学

去 [**Releases**](https://github.com/XIRANZHANG-X/scholarscope-ai/releases) 下载
`scholarscope-csv.zip`，解压就是 19 个 CSV 文件，UTF-8 编码，带表头。

**Tableau**：直接「连接 → 文本文件」选 `works.csv` 即可。要做关联的话，
用 `work_id` 把 `works.csv` 和 `work_authors.csv`、`work_countries.csv` 连起来；
用 `country_code` 把 `work_countries.csv` 和 `country_indicators_wide.csv` 连起来。

**Excel**：注意用「数据 → 从文本/CSV」导入并选 UTF-8，
直接双击打开中文和特殊字符会乱码。`work_abstracts.csv` 有 2 万行摘要，Excel 打开会很慢，按需再开。

**Python / R**：

```python
import pandas as pd
works = pd.read_csv("works.csv", parse_dates=["publication_date"])
```

```r
works <- readr::read_csv("works.csv")
```

**不需要装 Docker，不需要数据库，不需要 Python 环境。**

### 路线 B：要完整数据库（能写 SQL、改数据、跑项目代码）

```bash
git clone https://github.com/XIRANZHANG-X/scholarscope-ai.git
cd scholarscope-ai
uv sync                         # 装依赖（需要先装 uv）
cp .env.example .env            # 改掉里面的密码
docker compose up -d --wait     # 起 PostgreSQL
```

然后从 Releases 下载 `scholarscope.dump`（13 MB），恢复进去：

```bash
docker compose exec -T postgres pg_restore -U scholarscope -d scholarscope --clean --if-exists --no-owner < scholarscope.dump
```

想用图形界面点着看表：`docker compose --profile ui up -d`，然后开 <http://127.0.0.1:5050>，
左边 **Servers → ScholarScope (local)** 已经配好了，密码就是你 `.env` 里的 `POSTGRES_PASSWORD`。
表在 **Databases → scholarscope → Schemas → core / bridge / external / meta → Tables**。

嫌树太深就用 **Tools → Query Tool**（`Alt+Shift+Q`）直接写 SQL：

```sql
SELECT * FROM core.works LIMIT 100;
```

### 路线 C：自己从头采一遍（想了解 ETL 过程）

```bash
uv run scholarscope migrate
uv run scholarscope probe                       # 探查规模
uv run scholarscope ingest --profile standard   # 全部论文，约 $0.16、7 分钟
uv run scholarscope ror                         # ROR 机构富化，免费、约 9 分钟
uv run scholarscope worldbank                   # World Bank 指标，免费、约 3 秒
uv run scholarscope quality                     # 质量检查
```

OpenAlex 的免费 key 在 <https://openalex.org/settings/api> 领，每天 $1 额度。
命令详情见 [`docs/development.md`](docs/development.md)。

CSV 是用 [`scripts/export_csv.py`](scripts/export_csv.py) 从数据库导出的，数据更新后重跑一遍就行。

---

## 三、可以做什么——这部分开放讨论

下面是我看数据时觉得能做的方向，**不是任务分配，只是起个头**。
有想法的直接开 issue，或者在群里说。

**关于这个领域本身**
- RAG 是什么时候、以多快的速度从边缘变成主流的？和 LLM / Agents / Foundation Models 比呢？
- 这个领域的「思想源头」是什么？（语料里 90.9% 的引用指向外部，统计被引最多的外部论文就能看出来）
- 预印本比期刊论文领先多久？这个领域是不是真的「跑得特别快」？
- 哪些主题/关键词组合是新冒出来的？有没有能回测的增长信号？

**关于谁在做**
- 国家和机构的排名，以及谁在追赶谁
- 高校 vs 企业的比例，以及它们关注的主题有没有差别
- 国际合作有多普遍？哪些国家之间合作最多？（有经纬度，可以画地图连线）
- 作者层面：有没有一批人持续在做，还是大量一次性参与者？

**关于和国家实力的关系**
- 研发投入占 GDP 的比例，和论文产出有没有相关性？
- 按人口归一化之后，排名会变成什么样？（大国优势会消失，小而强的国家会浮出来）
- 收入分组、地区维度上有什么差别？

**关于数据本身（这块其实是课程的加分项）**
- 三个数据源融合之后，覆盖率提升了多少？哪些还是补不上？
- 41.4% 的论文没有国家信息，这会不会让所有国别结论都有偏差？偏向谁？

**技术方向**（想做深的话）
- 把论文向量化，做语义检索和相似论文推荐（数据库已装 pgvector）
- 给论文的 RAG 子方向做多标签分类（需要先定义类别 + 人工标注一批）
- 论文影响力预测
- 把引用网络导进 Neo4j 做图分析（compose 里已经配好 Neo4j，`--profile graph` 启动）

**要讨论的问题**
- 工具用什么？Tableau / Python+Plotly / R+ggplot 都行，也可以混用
- 怎么分工？按分析主题分，还是按技术栈分？
- PPT 正文限 20 页以内，哪些内容必须留、哪些砍掉？

---

## 四、仓库里有什么

```
├── README.md              ← 本文件
├── docs/数据字典.md        ← 每个字段的含义、覆盖率、数据坑  ★ 动手前读这个
├── docs/development.md     ← 命令、退出码、费用
├── 任务架构.md             ← 原始设计文档（很长）
├── scripts/export_csv.py   ← 把数据库导成 CSV
├── src/scholarscope/       ← 采集与清洗代码（Python）
├── sql/migrations/         ← 数据库表结构（3 个 migration）
├── tests/                  ← 117 项自动化测试
└── compose.yaml            ← PostgreSQL + pgAdmin + Neo4j
```

---

## 五、几条规矩

- **`.env` 永远不要提交**，里面有数据库密码。模板是 `.env.example`。
- **`data/` 不提交**，几十 MB，从 Releases 下载或用命令重新生成。
- **改数据库结构要写 migration**，不要手工改表，做法见 `docs/development.md`。
- **改了 `src/` 的代码，提交前跑一下 `uv run pytest`**，现在 117 项全过。
- 纯做分析、画图、写 SQL 的话以上都不用管——下载 CSV 就行。
