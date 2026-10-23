# 分析图表

五个分析方向的图都在这里。每张图配三样东西，方便接手修改：

| 文件 | 是什么 |
|---|---|
| `sql/figN_*.sql` | 取数的 SQL。**数据只从这里进图。** |
| `output/figN_*.csv` | 这条 SQL 的查询结果，也就是图上画的全部数据。可以直接用 Excel 或 Tableau 打开重画。 |
| `output/figN_*.png` | 图本身，300 dpi、4000×2250（16:9 满页），可以直接放进 PPT；同名 `.pdf` 是矢量版。 |

**怎么运行见下文「怎么重新生成」，完整流程见 [`../docs/流程.md`](../docs/流程.md)。**
每个方向自己的 `README.md` 逐图说明了：画的是什么、CSV 每列的含义、图上能直接读出的事实、数据坑。

---

## 图表清单

| 方向 | 目录 | 图 |
|---|---|---|
| 方向 | 目录 | 图 | 张数 |
|---|---|---|---:|
| 一、RAG 与其他大模型主题 | [`01_theme_comparison/`](01_theme_comparison/README.md) | 四主题逐年论文数 · 增长倍数（柱状、热力图、共轴折线）· 检索短语合并图 · 每 100 篇 LLM 论文对应的 RAG 论文 | 6 |
| 二、预印本 / 会议 / 期刊 | [`02_publication_types/`](02_publication_types/README.md) | 按季度的类型构成 · 类型占比（逐年、各主题）· 被引比例 · 平均被引 · 三种类型各自的载体排行 | 8 |
| 三、世界分布与合作 | [`03_world_collaboration/`](03_world_collaboration/README.md) | 世界地图 · 前 15 名国家 · 国际合作比例 · 份额逐年 · 合作矩阵 · 合作连线 · 覆盖率（按类型、按年份） | 8 |
| 四、子方向 | [`04_subfields/`](04_subfields/README.md) | 子方向规模（方法、应用各一张）· 占比变化哑铃图 · 共现热图 | 4 |
| 五、国家投入与产出 | [`../country_indicators_analysis/`](../country_indicators_analysis/README.md) | 单指标趋势线 5 张 · 实际值对预测值 2 张 · 回归系数 2 张 | 9 |

共 35 张，全部是一张图一个坐标系。样式统一规范见 [`图表统一规范.md`](图表统一规范.md)。
同样的 35 张图还有一个可交互的网页版，见下文「网页」。

---

## 怎么重新生成

都在仓库根目录执行，前提是数据库已经跑起来、台湾已经补入（完整步骤见 [`../docs/流程.md`](../docs/流程.md)）。

连数据库重新生成五个方向的全部 CSV 和图（`--strict`：连不上库就报错，保证结果来自数据库）：

```bash
uv run python analysis/run_all.py --strict
```

生成网页（见下文）：

```bash
uv run python site/build_site.py
```

只想调样式、不连数据库时，给任一脚本加 `--from-csv`，用 `output/` 里已有的 CSV 重画。
改图的位置：一张图一个函数，函数名与图片文件名相同；所有样式在 [`common.py`](common.py)。

分析脚本用的是只读数据库连接，不会改动数据库里的任何东西。

---

## 所有图共用的约定

完整规范见 [`图表统一规范.md`](图表统一规范.md)，要点：

- **图上的文字是英文**，标题居中、简短；说明文档是中文。
- **2026* 只到 9 月 22 日**：折线该段画虚线、末点空心，柱子画斜线填充。
- **颜色跟着对象走**：RAG / 预印本蓝，LLM / 会议论文橙，Agents / 期刊文章绿，Foundation models / 综述黄。
- **最高 / 最低值用粗黑描边加 Highest / Lowest 标注。**
- **图上没有脚注**：口径说明都写在各方向的 README 里，引用图时请一起看。

## 网页：一个双击就能打开的 HTML

`site/build_site.py` 连数据库、执行和出图脚本同一批 SQL，把 35 张图的可交互版（Plotly）和数据一起打包进
`site/dist/scholarscope.html`（约 6 MB，不进 git）。这个文件不需要服务器、也不需要联网，可以直接发群、
放进 Release 或 GitHub Pages。它是构建那一刻的快照，数据更新后重跑一次即可。

- `site/charts.py`：网页版的每张图（配色、口径与 PNG 一致）。
- `site/template.html.j2`、`site/tokens.css`：页面版式（用 Hallmark 设计，英文）。
- `site/sql/overview.sql`：页面顶部的语料规模。
- 不连库只调版式：`uv run python site/build_site.py --from-csv`（页脚会标明这是 CSV 预览）。

## 所有方向都会碰到的数据坑

完整清单见 [`docs/数据字典.md`](../docs/数据字典.md)。和这些图直接相关的：

1. **2026 年只有不到 9 个月。** 比较年度总数时 2026 年会偏低，比较占比时影响小得多。
2. **41% 的论文没有国家信息**，而且预印本缺得最多。见方向三的 fig6a、fig6b。
3. **会议论文进 OpenAlex 有几个月的滞后**，最近两个季度的会议论文数是偏低的。
4. **15.2% 的论文日期只精确到年**，被记成 1 月 1 日。按月或按季度画图前要剔除。
5. **LLM、Agents、Foundation models 只有计数没有论文**，而且是几个检索短语相加，是上限。
6. **Release 里的 `theme_year_counts.csv` 把 RAG 算了两遍。** 方向一用自己的 SQL，不受影响。
