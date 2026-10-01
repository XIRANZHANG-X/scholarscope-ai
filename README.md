# ScholarScope AI

NTU 课程项目：用 OpenAlex + ROR + World Bank 三个数据源，分析 **RAG（检索增强生成）** 这个研究方向
是怎么从 2019 年的 4 篇论文长到 2026 年的一万多篇的，谁在做、在哪做、和国家的科研投入有没有关系。

**截止日期：2026-10-23。** 设计全文见 [`任务架构.md`](任务架构.md)。

---

## 现在做到哪了

**数据基座已经完成**——不是"写好了代码"，是数据库里真的有数据，而且每个数字都是实测的。

| | 已入库 |
|---|---|
| 论文 | **15,921 篇**（RAG 主题，2019-01-01 至 2026-09-22，计算机领域全量） |
| 作者位 | 68,797 条，涉及 48,101 位作者 |
| 引用 | 104,227 条（其中 9,446 条指向语料内部） |
| 机构 | 5,684 家 OpenAlex 机构 ↔ **5,747 家 ROR 标准机构**，1,665 对上下级关系 |
| 国家指标 | **217 个国家 × 6 个 World Bank 指标 × 7 年 = 9,114 行** |
| 数据质量 | 13 项指标自动记录，4 道质量门全部通过 |

工程上：PostgreSQL 17 + pgvector（Docker）、4 个 schema 33 张表和视图、5 个 CLI 命令、
**117 项自动化测试**、断点续传、重跑幂等、每行数据可追溯到写它的那次运行。

采集总花费 **$0.16**（OpenAlex 按请求计费；ROR 和 World Bank 免费）。

### 还没开始的

分析层、机器学习、检索、Agent、Streamlit 界面、PPT —— 见下面的分工。
数据库里 `analytics`、`ml`、`audit` 三个 schema 还没建。

---

## 15 分钟跑起来

需要先装：[Docker Desktop](https://www.docker.com/products/docker-desktop/)、
[uv](https://docs.astral.sh/uv/)、Git。

```bash
git clone <仓库地址> && cd Database
uv sync                         # 装依赖（锁定版本，不会装歪）
cp .env.example .env            # 然后把里面的密码改掉
docker compose up -d --wait     # 起 PostgreSQL
uv run scholarscope migrate     # 建表
uv run pytest -m "not db"       # 59 项单元测试，不需要数据库，用来确认环境没问题
```

**然后你需要数据。两条路：**

**A. 要一份数据库备份（推荐，5 分钟）** —— 跟我要 `scholarscope.dump`，然后：

```bash
docker compose exec -T postgres pg_restore -U scholarscope -d scholarscope --clean --if-exists < scholarscope.dump
```

**B. 自己重新采（约 20 分钟，需要 OpenAlex key）**

```bash
uv run scholarscope probe                           # 探查规模，约 $0.0004
uv run scholarscope ingest --profile standard       # 下载全部论文，约 $0.16、7 分钟
uv run scholarscope ror                             # ROR 富化，免费、约 9 分钟
uv run scholarscope worldbank                       # World Bank 指标，免费、约 3 秒
uv run scholarscope quality                         # 质量检查
```

OpenAlex 的免费 key 在 <https://openalex.org/settings/api> 领，每天 $1 额度。
不填 key 也能跑，但每天只有 $0.10，只够 probe 和 smoke。

命令的详细说明、退出码、费用见 [`docs/development.md`](docs/development.md)。

### 用图形界面看数据

```bash
docker compose --profile ui up -d     # 多起一个 pgAdmin
```

然后打开 <http://127.0.0.1:5050>。左边 **Servers → ScholarScope (local)** 已经预先配好了，
点开时输入你 `.env` 里的 `POSTGRES_PASSWORD` 即可。表在
**Databases → scholarscope → Schemas → 选一个 schema → Tables**，
右键任意表 → *View/Edit Data* → *All Rows* 就能看到内容。

不想装图形界面的话，命令行一样能看：

```bash
docker compose exec postgres psql -U scholarscope -d scholarscope
```

常用命令：`\dn` 列出 schema、`\dt core.*` 列出 core 里的表、`\d core.works` 看某张表的字段、
`\q` 退出。

PyCharm Professional 和 DataGrip 自带数据库工具，连接参数是
`127.0.0.1:5432`、数据库 `scholarscope`、用户 `scholarscope`。

---

## 分工：剩下的五块

数据基座（下表 A）已完成。**B 和 C 是必须做完的主线**，D 和 E 是加分项，F 是交付物。

| 块 | 内容 | 产出 | 建议人数 |
|---|---|---|---|
| **A. 数据基座** | ✅ 已完成 | 上面那张表 | — |
| **B. SQL 分析层** | 建 `analytics` schema，把每张图要的数字写成物化视图；刻意用上窗口函数、CTE、递归 CTE、条件聚合、`EXPLAIN ANALYZE` | PPT 第 7–8 页 | 1–2 |
| **C. 分析与可视化** | 11 张图，**一图一脚本**，每张图能单独改、单独重跑 | PPT 第 9–12 页 | 1–2 |
| **D. 机器学习** | RAG 子方向多标签分类、趋势回测、影响力预测 | PPT 第 13–14 页 | 1–2 |
| **E. 检索与 Agent** | pgvector 向量化、混合检索、Research Agent 与评测 | PPT 第 15–17 页 | 1 |
| **F. 界面与 PPT** | Streamlit 页面 + 20 页以内的 PPT 成稿 | 最终交付 | 1–2 |

### 每块的第一件事

- **B**：读 `任务架构.md` §7.8（"需要展示的 SQL 能力"清单）和 §9.1，先把"国家排名"这一张图需要的
  SQL 写出来跑通，确定查询怎么存、怎么版本化。
- **C**：图表清单草稿在下面，等 B 的第一个视图出来就能开工。**技术选型已定：Plotly**（已在依赖里）。
- **D**：**这块现在就要开始，因为它卡在人工上。** 先把 RAG 子方向的类别定出来（`任务架构.md` §5.2
  有候选清单，需要全组确认），然后标注一批论文。类别没定，模型一行都写不了。
- **E**：依赖 B 的 SQL，可以先搭 `LLMProvider` 抽象和本地 Ollama 的连通性测试。
- **F**：最后做，但 PPT 的骨架（`任务架构.md` §16 有 18 页的建议结构）现在就能搭起来占位。

### 图表清单（草稿，共 11 张）

| # | 图 | 状态 |
|---|---|---|
| 1 | 四大主题（FM/LLM/Agents/RAG）年度发文对比 | 画法已定：**对数轴** |
| 2 | RAG 语料月度趋势 | 画法待定 |
| 3 | 国家发文排名 + 世界地图 | 待讨论 |
| 4 | 机构排名 Top 20 | 待讨论 |
| 5 | 国家研发投入 vs 科研产出（散点） | 待讨论 |
| 6 | 国际合作网络（地图连线） | 待讨论 |
| 7 | 语料内引用网络 | 待讨论 |
| 8 | 被引最多的参考文献 Top 20 | 待讨论 |
| 9 | 开放获取与引用分布 | 待讨论 |
| 10 | 数据覆盖率与缺口 | 待讨论 |
| 11 | 子方向演化与 RAG × Agent | **依赖 D，最后做** |

---

## 动手前必读：这份数据已知的 8 个坑

每一条都是查实的，不是猜的。踩任何一条都会画出错误的图。

1. **2,417 篇（15.2%）论文的日期是 1 月 1 日**，因为 OpenAlex 只知道年份。按月画图会出现假的"一月高峰"。
2. **会议论文进索引慢**：2026 年 4 月有 226 篇，9 月只有 15 篇。这是没补齐，不是真的下跌。
3. **2026 年只有 9 个月**（采到 9 月 22 日）。
4. **41.4% 的论文没有国家信息**，41.8% 没有机构——主因是这些论文的作者位连原始单位字符串都没有，
   任何匹配算法都救不回来。
5. **台湾（156 篇）在 World Bank 里没有记录**，所以没有 ISO-3 代码和经纬度，
   **在地图上会悄悄消失**，必须手工补。
6. **研发类指标只能用到 2023 年**（2024 年全球只有 30 个国家报数，2025 年为零）。
7. **90.9% 的参考文献指向语料之外**，语料内部只有 9,446 条引用边。
8. **四大主题的计数会重复**：一篇讲"用 RAG 做 LLM agent"的论文在三个主题里各算一次。
   去重做不到——我们只下载了 RAG 的论文，另外三个主题只有计数。

还有一条**数据契约**：`external` 里的数据是边跑边提交的，所以一次标记为 `failed` 的运行
**也可能留下完全正确的数据行**（现在就有 16 条）。查询时**不要用 `status = 'succeeded'` 过滤事实行**，
否则会凭空丢数据。

---

## 规矩

- **`.env` 永远不提交。** 里面有数据库密码和 OpenAlex key。模板是 `.env.example`。
- **`data/` 不提交。** 原始 API 数据几十 MB，用命令重新生成，或者要备份。
- **改数据库结构要写 migration**，不要手工改表：`uv run alembic revision -m "..." --rev-id 000N`，
  然后扩展 `tests/integration/test_migrations.py`。做法见 `docs/development.md`。
- **提交前跑测试**：`uv run pytest`。现在是 117 项全过，别让它变红。
- 分析代码**只能通过数据库拿数据**（这是课程硬性要求），不要在 Python 里读 CSV 绕过去。
