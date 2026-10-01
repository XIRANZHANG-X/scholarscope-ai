"""Generate a single self-contained HTML tour of the corpus, for deciding what to analyse.

    uv run python scripts/data_overview.py [OUT.html]

Every number comes from a SQL query against the project database — the file is a snapshot of
those queries, not a separate copy of the data. Plotly is inlined so the page works offline and
can simply be sent to someone.

This is an exploration aid, not a source of final figures: anything that goes into the deck
should be re-derived from the database by its own script.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import plotly.graph_objects as go
import plotly.io as pio

from scholarscope.config import Settings
from scholarscope.db import connect

INK = "#1f2933"
MUTED = "#6b7280"
GRID = "rgba(31,41,51,.10)"
BLUE, ORANGE, GREEN, PURPLE, GREY = "#2f6fed", "#e8684a", "#17a673", "#8b5cf6", "#9aa5b1"

LAYOUT = dict(
    paper_bgcolor="white", plot_bgcolor="white",
    font=dict(color=INK, size=13, family="-apple-system, Segoe UI, Roboto, 'Microsoft YaHei', sans-serif"),
    margin=dict(l=60, r=24, t=16, b=48),
    xaxis=dict(gridcolor=GRID, zeroline=False),
    yaxis=dict(gridcolor=GRID, zeroline=False),
    legend=dict(orientation="h", y=1.12, x=0),
    hoverlabel=dict(font_size=12),
)


def rows(conn, sql: str, params=None) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


def col(data: list[tuple], i: int) -> list:
    return [r[i] for r in data]


# --------------------------------------------------------------------------------------------
# Section builders. Each returns (title, figure, lead paragraph, "what you could do" prompt).
# --------------------------------------------------------------------------------------------

def fig_themes(conn):
    data = rows(conn, """
        SELECT theme, bucket::int AS year, sum(works_count) AS works
        FROM meta.recall_probes
        WHERE dimension = 'publication_year' AND field_scope = 'cs'
        GROUP BY 1, 2 ORDER BY 1, 2
    """)
    names = {"llm": "LLM", "agents": "Agents", "rag": "RAG", "foundation_models": "Foundation Models"}
    colours = {"llm": GREY, "agents": BLUE, "rag": ORANGE, "foundation_models": PURPLE}
    fig = go.Figure()
    for key, label in names.items():
        series = [(y, w) for t, y, w in data if t == key]
        fig.add_scatter(x=col(series, 0), y=col(series, 1), name=label, mode="lines+markers",
                        line=dict(color=colours[key], width=3.5 if key == "rag" else 2))
    fig.update_layout(**LAYOUT)
    fig.update_yaxes(type="log", title="论文数（对数轴）")
    fig.update_xaxes(dtick=1)
    return (
        "RAG 在大模型浪潮里的位置",
        fig,
        "四个主题在计算机领域的年度论文数。纵轴是对数轴——否则 LLM 一条线会把其余三条压在底部。"
        "RAG 在 2023→2024 之间几乎垂直上升，一年翻了约 15 倍。",
        "这四条线的「起飞时间」差了多久？RAG 是跟着 LLM 起来的，还是滞后了一段？"
        "如果按增长率而不是绝对量排，谁最快？",
    )


def fig_monthly(conn):
    data = rows(conn, """
        SELECT to_char(date_trunc('month', publication_date), 'YYYY-MM') AS month,
               count(*) FILTER (WHERE to_char(publication_date, 'MM-DD') <> '01-01') AS dated,
               count(*) FILTER (WHERE to_char(publication_date, 'MM-DD') =  '01-01') AS year_only
        FROM core.works WHERE publication_date >= '2023-01-01'
        GROUP BY 1 ORDER BY 1
    """)
    fig = go.Figure()
    fig.add_bar(x=col(data, 0), y=col(data, 1), name="有确切月份", marker_color=BLUE)
    fig.add_bar(x=col(data, 0), y=col(data, 2), name="只知道年份（被记为 1 月）", marker_color=GREY)
    fig.update_layout(**LAYOUT, barmode="stack")
    fig.update_yaxes(title="论文数")
    fig.update_xaxes(tickangle=-45, nticks=14)
    return (
        "RAG 语料的月度节奏",
        fig,
        "灰色那截是 OpenAlex 只知道年份、不知道月份的论文（共 2,417 篇，占 15.2%），它们被统一记成 1 月 1 日。"
        "把它们单独染色，是为了让「一月高峰」这个假象无处藏身——直接按月画图会以为大家都在一月发论文。",
        "剔除灰色部分之后，增长曲线是什么形状？有没有拐点？"
        "2026 年只统计到 9 月 22 日，做年度对比时怎么处理这个不完整的年份？",
    )


def fig_types(conn):
    data = rows(conn, """
        SELECT to_char(date_trunc('quarter', publication_date), 'YYYY"Q"Q') AS quarter,
               sum((type = 'preprint')::int)         AS preprint,
               sum((type = 'conference-paper')::int) AS conference,
               sum((type = 'article')::int)          AS article,
               sum((type = 'review')::int)           AS review
        FROM core.works
        WHERE publication_date >= '2024-01-01' AND to_char(publication_date, 'MM-DD') <> '01-01'
        GROUP BY 1 ORDER BY 1
    """)
    fig = go.Figure()
    for i, (label, colour) in enumerate(
        [("预印本", BLUE), ("会议论文", GREEN), ("期刊论文", ORANGE), ("综述", PURPLE)], start=1
    ):
        fig.add_bar(x=col(data, 0), y=col(data, i), name=label, marker_color=colour)
    fig.update_layout(**LAYOUT, barmode="stack")
    fig.update_yaxes(title="论文数")
    return (
        "预印本、会议、期刊的此消彼长",
        fig,
        "按季度拆分（已剔除只知道年份的论文）。预印本长期领先，说明这个领域的节奏比传统学科快得多。",
        "⚠️ 最后两个季度的会议论文看着像断崖，那是<strong>索引滞后</strong>，不是真的减少——"
        "会议论文进 OpenAlex 要几个月。可以做的：预印本比正式发表平均领先多久？"
        "有多少预印本最终变成了会议或期刊论文？",
    )


def fig_country_map(conn):
    data = rows(conn, """
        SELECT wc.country_code, coalesce(p.iso3_code, '') AS iso3,
               coalesce(p.name, c.name, wc.country_code) AS name, count(DISTINCT wc.work_id) AS works
        FROM bridge.work_countries wc
        LEFT JOIN external.country_profiles p USING (country_code)
        LEFT JOIN core.countries c USING (country_code)
        GROUP BY 1, 2, 3 ORDER BY 4 DESC
    """)
    # Taiwan has no World Bank profile, so no ISO-3; without this it silently vanishes from the map.
    iso3 = {r[0]: (r[1] or {"TW": "TWN"}.get(r[0], "")) for r in data}
    plotted = [r for r in data if iso3[r[0]]]
    fig = go.Figure(go.Choropleth(
        locations=[iso3[r[0]] for r in plotted], z=[r[3] for r in plotted],
        text=[r[2] for r in plotted], colorscale="Blues", marker_line_color="white",
        marker_line_width=0.4, colorbar=dict(title="论文数", thickness=12),
        hovertemplate="%{text}<br>%{z} 篇<extra></extra>",
    ))
    fig.update_layout(
        **{k: v for k, v in LAYOUT.items() if k not in {"xaxis", "yaxis"}},
        geo=dict(showframe=False, projection_type="natural earth", bgcolor="white",
                 showcountries=True, countrycolor="rgba(31,41,51,.15)", landcolor="#f3f5f7"),
        height=420,
    )
    top = "、".join(f"{r[2]} {r[3]:,}" for r in data[:5])
    return (
        "谁在做：世界分布",
        fig,
        f"按论文的作者机构所在国家统计，一篇多国论文在每个国家各计一次。前五名：{top}。",
        "⚠️ 两个坑：<strong>41.4% 的论文根本没有国家信息</strong>（作者位上连单位原文都没有），"
        "所以这是「有国家信息的那 58.6%」的分布；<strong>台湾在 World Bank 里没有记录</strong>，"
        "这张图是手工补上 ISO-3 才画出来的，不补它会凭空消失。<br>"
        "可以做的：按人口或 GDP 归一化之后，排名会变成什么样？小国会不会跳出来？",
    )


def fig_institutions(conn):
    data = rows(conn, """
        SELECT coalesce(o.display_name, i.display_name) AS name,
               coalesce(i.type, 'unknown') AS kind, count(DISTINCT wi.work_id) AS works
        FROM bridge.work_institutions wi
        JOIN core.institutions i USING (institution_id)
        LEFT JOIN external.institution_crosswalk c USING (institution_id)
        LEFT JOIN external.ror_organizations o ON o.ror_id = coalesce(i.ror_id, c.ror_id)
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20
    """)
    data = data[::-1]
    palette = {"education": BLUE, "company": ORANGE, "government": GREEN, "nonprofit": PURPLE}
    fig = go.Figure(go.Bar(
        x=col(data, 2), y=col(data, 0), orientation="h",
        marker_color=[palette.get(k, GREY) for k in col(data, 1)],
        text=col(data, 2), textposition="outside",
        hovertemplate="%{y}<br>%{x} 篇<extra></extra>",
    ))
    fig.update_layout(**LAYOUT, height=640, showlegend=False)
    fig.update_xaxes(title="论文数")
    fig.update_yaxes(automargin=True)
    return (
        "谁在做：机构 Top 20",
        fig,
        "名称来自 ROR 标准化，所以同一机构的不同写法已经合并。"
        "<span style='color:#2f6fed'>蓝＝高校</span>、"
        "<span style='color:#e8684a'>橙＝企业</span>、"
        "<span style='color:#17a673'>绿＝政府机构</span>、"
        "<span style='color:#8b5cf6'>紫＝非营利</span>。",
        "高校和企业的占比是多少？它们研究的主题有没有系统性差别——"
        "比如企业更偏工程落地、高校更偏方法创新？这个用主题标签就能验证。",
    )


def fig_collaboration(conn):
    data = rows(conn, """
        SELECT coalesce(pa.name, a.country_code) AS c1, coalesce(pb.name, b.country_code) AS c2,
               count(*) AS works
        FROM bridge.work_countries a
        JOIN bridge.work_countries b ON a.work_id = b.work_id AND a.country_code < b.country_code
        LEFT JOIN external.country_profiles pa ON pa.country_code = a.country_code
        LEFT JOIN external.country_profiles pb ON pb.country_code = b.country_code
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15
    """)[::-1]
    total, multi = rows(conn, """
        SELECT count(DISTINCT work_id),
               count(*) FILTER (WHERE n > 1)
        FROM (SELECT work_id, count(*) AS n FROM bridge.work_countries GROUP BY 1) t
    """)[0]
    fig = go.Figure(go.Bar(
        x=col(data, 2), y=[f"{a} ↔ {b}" for a, b, _ in data], orientation="h",
        marker_color=GREEN, text=col(data, 2), textposition="outside",
    ))
    fig.update_layout(**LAYOUT, height=520, showlegend=False)
    fig.update_xaxes(title="合作论文数")
    fig.update_yaxes(automargin=True)
    return (
        "国际合作：最常一起发论文的国家",
        fig,
        f"在有国家信息的 {total:,} 篇论文里，{multi:,} 篇（{multi / total:.1%}）涉及一个以上的国家。",
        "机构表里有经纬度，可以把这个画成世界地图上的连线图，视觉效果比条形图强得多。"
        "还可以做：国际合作的论文是不是引用更高？合作比例这几年在上升还是下降？",
    )


def fig_topics(conn):
    data = rows(conn, """
        SELECT t.subfield_name, count(*) AS works
        FROM bridge.work_topics wt JOIN core.topics t USING (topic_id)
        WHERE wt.is_primary AND t.subfield_name IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """)[::-1]
    fig = go.Figure(go.Bar(x=col(data, 1), y=col(data, 0), orientation="h",
                           marker_color=PURPLE, text=col(data, 1), textposition="outside"))
    fig.update_layout(**LAYOUT, height=520, showlegend=False)
    fig.update_xaxes(title="论文数（按主要主题）")
    fig.update_yaxes(automargin=True)
    return (
        "研究什么：子领域分布",
        fig,
        "每篇论文取 OpenAlex 判定的「主要主题」所属子领域。这是 OpenAlex 的通用分类，"
        "不是 RAG 自己的子方向——后者需要我们自己定义和标注。",
        "通用分类看不出 RAG 内部的分化（比如「检索优化」「多模态 RAG」「评测」）。"
        "想看 RAG 自己的子方向，就要先定义类别再标注一批论文，这是个需要全组参与的活。"
        "另一条省力的路：用关键词共现做无监督聚类，先看看数据自己会分成几堆。",
    )


def fig_citations(conn):
    buckets = rows(conn, """
        SELECT CASE WHEN cited_by_count = 0 THEN '0'
                    WHEN cited_by_count BETWEEN 1 AND 2 THEN '1-2'
                    WHEN cited_by_count BETWEEN 3 AND 9 THEN '3-9'
                    WHEN cited_by_count BETWEEN 10 AND 49 THEN '10-49'
                    WHEN cited_by_count BETWEEN 50 AND 199 THEN '50-199'
                    ELSE '200+' END AS bucket,
               count(*) AS works
        FROM core.works GROUP BY 1
    """)
    order = ["0", "1-2", "3-9", "10-49", "50-199", "200+"]
    counts = {b: w for b, w in buckets}
    fig = go.Figure(go.Bar(x=order, y=[counts.get(b, 0) for b in order], marker_color=ORANGE,
                           text=[f"{counts.get(b, 0):,}" for b in order], textposition="outside"))
    fig.update_layout(**LAYOUT, showlegend=False)
    fig.update_yaxes(title="论文数")
    fig.update_xaxes(title="被引次数")
    zero = counts.get("0", 0)
    return (
        "影响力：引用的长尾",
        fig,
        f"{zero:,} 篇（{zero / 15921:.0%}）还没有被引用过一次。这在一个爆发式增长的领域里很正常——"
        "大部分论文是最近一年发的，还来不及被引。",
        "⚠️ 直接比较不同年份的引用数是不公平的，老论文天生占便宜。"
        "数据里有 <code>fwci</code>（领域年份归一化影响力）和 <code>citation_percentile</code> 两个字段，"
        "就是为了解决这个问题，但较新的论文这两个字段是空的。"
        "可以做的：控制住发表时间之后，什么样的论文更容易被引？开放获取有没有加成？",
    )


def fig_oa(conn):
    data = rows(conn, """
        SELECT coalesce(oa_status, 'unknown') AS status, count(*) AS works
        FROM core.works GROUP BY 1 ORDER BY 2 DESC
    """)
    labels = {"gold": "Gold 金色", "green": "Green 绿色", "hybrid": "Hybrid 混合",
              "bronze": "Bronze 青铜", "diamond": "Diamond 钻石", "closed": "Closed 封闭"}
    fig = go.Figure(go.Pie(
        labels=[labels.get(s, s) for s, _ in data], values=col(data, 1), hole=0.5,
        marker_colors=[ORANGE, GREEN, BLUE, "#d4a017", PURPLE, GREY],
        textinfo="label+percent",
    ))
    fig.update_layout(**{k: v for k, v in LAYOUT.items() if k not in {"xaxis", "yaxis"}}, height=420)
    return (
        "开放获取的构成",
        fig,
        "Green 指作者自存档（比如放 arXiv），Gold 指发在完全开放的期刊，Closed 指需要付费订阅。",
        "这个领域的开放程度和传统学科比如何？不同国家、不同机构类型的开放比例有差别吗？"
        "开放获取的论文是不是引用更多——这是个经典问题，数据刚好够回答。",
    )


def fig_rd_vs_output(conn):
    data = rows(conn, """
        WITH output AS (
            SELECT country_code, count(DISTINCT work_id) AS works
            FROM bridge.work_countries GROUP BY 1
        ),
        -- R&D data stops around 2023, so take the most recent value available per country.
        rd AS (
            SELECT DISTINCT ON (country_code) country_code, value, year
            FROM external.country_indicators
            WHERE indicator_code = 'GB.XPD.RSDV.GD.ZS' AND value IS NOT NULL
            ORDER BY country_code, year DESC
        )
        SELECT p.name, o.works, rd.value, rd.year, p.region
        FROM output o
        JOIN rd USING (country_code)
        JOIN external.country_profiles p USING (country_code)
        WHERE o.works >= 5 ORDER BY o.works DESC
    """)
    fig = go.Figure(go.Scatter(
        x=col(data, 2), y=col(data, 1), mode="markers+text",
        text=[n if w > 180 else "" for n, w, *_ in data], textposition="top center",
        textfont=dict(size=10, color=MUTED),
        marker=dict(size=10, color=BLUE, opacity=.65, line=dict(width=0)),
        customdata=[[n, y] for n, _, _, y, _ in data],
        hovertemplate="%{customdata[0]}<br>研发投入 %{x:.2f}% GDP（%{customdata[1]} 年）<br>%{y} 篇<extra></extra>",
    ))
    fig.update_layout(**LAYOUT, height=480, showlegend=False)
    fig.update_yaxes(type="log", title="RAG 论文数（对数轴）")
    fig.update_xaxes(title="研发投入占 GDP 比例 (%)，各国最近可得年份")
    return (
        "国家投入 vs 科研产出",
        fig,
        f"只画了论文数 ≥5 的 {len(data)} 个国家。横轴是研发投入占 GDP 的比例，取各国最近一个有数据的年份。"
        "这是整份数据里<strong>唯一需要三个数据源同时到位</strong>才画得出的图——"
        "OpenAlex 给论文、ROR 给机构的国家、World Bank 给指标。",
        "⚠️ 研发数据只覆盖约 31% 的国家-年份，而且基本停在 2023 年，所以这张图是「有数据的那部分国家」。"
        "可以做的：这个相关性有多强？按人均 GDP 或研究人员数量换个横轴呢？"
        "哪些国家明显在「线之上」（投入少产出多）？",
    )


def fig_sources(conn):
    data = rows(conn, """
        SELECT s.display_name, count(*) AS works
        FROM core.works w JOIN core.sources s USING (source_id)
        GROUP BY 1 ORDER BY 2 DESC LIMIT 12
    """)[::-1]
    fig = go.Figure(go.Bar(x=col(data, 1), y=col(data, 0), orientation="h", marker_color=BLUE,
                           text=col(data, 1), textposition="outside"))
    fig.update_layout(**LAYOUT, height=440, showlegend=False)
    fig.update_xaxes(title="论文数")
    fig.update_yaxes(automargin=True)
    return (
        "发在哪里：Top 12 载体",
        fig,
        "arXiv 一家独大是这个领域的典型特征——大家先挂预印本，正式发表是后面的事。",
        "顶会（NeurIPS、ACL、EMNLP 等）各占多少？"
        "不同载体的论文在引用和主题上有差别吗？",
    )


def fig_external_refs(conn):
    data = rows(conn, """
        SELECT r.referenced_work_id, count(*) AS cited
        FROM bridge.work_references r
        WHERE NOT EXISTS (SELECT 1 FROM core.works w WHERE w.work_id = r.referenced_work_id)
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """)
    titles = fetch_titles([r[0] for r in data])
    labels = [titles.get(wid, wid) for wid, _ in data]
    data_rev, labels_rev = data[::-1], labels[::-1]
    fig = go.Figure(go.Bar(
        x=[c for _, c in data_rev], y=labels_rev, orientation="h", marker_color=GREY,
        text=[c for _, c in data_rev], textposition="outside",
    ))
    fig.update_layout(**LAYOUT, height=560, showlegend=False)
    fig.update_xaxes(title="被语料内多少篇论文引用")
    fig.update_yaxes(automargin=True)
    return (
        "这个领域的思想源头",
        fig,
        "语料里 90.9% 的参考文献指向语料之外。统计这些外部论文被引用的次数，"
        "就能看出整个 RAG 领域共同站在谁的肩膀上。",
        "这些论文本身不在语料里（它们不是 RAG 主题），但它们定义了这个领域的起点。"
        "把它们按年份排开，就是一条「RAG 的前史」，可以直接做成 PPT 里「知识谱系」那一页。<br>"
        "⚠️ 标着「已删除或合并」的几条是个真实发现：<strong>OpenAlex 的 ID 并不永久稳定</strong>，"
        "我们 9 月 22 日记录的引用里，有几个 ID 现在已经查不到了。"
        "这说明任何依赖外部 ID 的分析都要记录采集日期——这本身就是数据质量那一页的好素材。",
    )


def fetch_titles(work_ids: list[str]) -> dict[str, str]:
    """Best-effort: these works sit outside the corpus, so only OpenAlex knows their titles.

    An id the lookup does not return has been merged or deleted upstream since we captured the
    reference lists — it is labelled as such rather than shown bare, because a lone `W…` on an
    axis reads like a rendering bug.
    """
    try:
        response = httpx.get(
            "https://api.openalex.org/works",
            params={"filter": f"openalex_id:{'|'.join(work_ids)}",
                    "select": "id,title,publication_year", "per-page": 50},
            timeout=20.0, headers={"User-Agent": "ScholarScope/0.1 (course project)"},
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - the report must still render offline
        print(f"  (could not reach OpenAlex, showing ids instead: {exc})")
        return {}

    out = {}
    for item in response.json()["results"]:
        short = item["id"].rsplit("/", 1)[-1]
        title = (item.get("title") or short)[:70]
        year = item.get("publication_year")
        out[short] = f"{title} ({year})" if year else title
    missing = [wid for wid in work_ids if wid not in out]
    if missing:
        print(f"  ({len(missing)} ids no longer resolve in OpenAlex: {', '.join(missing)})")
    return out | {wid: f"{wid}（OpenAlex 已删除或合并）" for wid in missing}


def fig_coverage(conn):
    data = rows(conn, """
        SELECT DISTINCT ON (check_name) check_name, failing_rows, total_rows
        FROM meta.data_quality_checks ORDER BY check_name, checked_at DESC
    """)
    labels = {
        "works_without_institution": "论文没有机构", "works_without_country": "论文没有国家",
        "works_missing_abstract": "论文没有摘要", "authorships_missing_author_id": "作者位没有作者 ID",
        "references_outside_corpus": "参考文献在语料外", "rd_indicator_missing": "国家研发指标缺失",
        "works_duplicate_doi": "DOI 重复", "institutions_without_ror": "机构没有 ROR",
        "corpus_countries_without_profile": "国家没有 World Bank 档案",
    }
    picked = [(labels[name], fail, total) for name, fail, total in data if name in labels]
    picked.sort(key=lambda r: r[1] / r[2])
    fig = go.Figure()
    fig.add_bar(x=[100 * (t - f) / t for _, f, t in picked], y=[r[0] for r in picked],
                orientation="h", name="有数据", marker_color=GREEN)
    fig.add_bar(x=[100 * f / t for _, f, t in picked], y=[r[0] for r in picked],
                orientation="h", name="缺失", marker_color=ORANGE,
                text=[f"{f:,} / {t:,}" for _, f, t in picked], textposition="inside",
                insidetextanchor="start", textfont=dict(color="white", size=11))
    fig.update_layout(**LAYOUT, barmode="stack", height=460)
    fig.update_xaxes(title="百分比", range=[0, 100], ticksuffix="%")
    fig.update_yaxes(automargin=True)
    return (
        "数据缺口：哪些地方不完整",
        fig,
        "这些数字是数据库自己记录的质量指标，不是事后估算的。"
        "橙色部分就是这份数据的边界——任何结论都只在绿色部分成立。",
        "<strong>这张图本身就是一个分析主题。</strong>课程把数据质量分析列为加分项，"
        "而「41.4% 的论文没有国家」这件事会不会让所有国别结论都偏向某些国家，"
        "是个可以真正验证的问题：比较有国家信息和没有国家信息的论文，它们在年份、类型、载体上有没有系统差别？",
    )


SECTIONS = [
    fig_themes, fig_monthly, fig_types, fig_country_map, fig_institutions,
    fig_collaboration, fig_topics, fig_sources, fig_citations, fig_oa,
    fig_external_refs, fig_rd_vs_output, fig_coverage,
]


def headline(conn) -> list[tuple[str, str]]:
    (works, authors, insts, refs, internal, countries) = rows(conn, """
        SELECT (SELECT count(*) FROM core.works),
               (SELECT count(*) FROM core.authors),
               (SELECT count(*) FROM core.institutions),
               (SELECT count(*) FROM bridge.work_references),
               (SELECT count(*) FROM bridge.work_references r
                  WHERE EXISTS (SELECT 1 FROM core.works w WHERE w.work_id = r.referenced_work_id)),
               (SELECT count(DISTINCT country_code) FROM bridge.work_countries)
    """)[0]
    return [
        (f"{works:,}", "篇论文"), (f"{authors:,}", "位作者"), (f"{insts:,}", "家机构"),
        (f"{refs:,}", "条引用"), (f"{internal:,}", "条语料内引用"), (f"{countries}", "个国家"),
    ]


CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; background:#f7f8fa; color:#1f2933;
  font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif; }
.wrap { max-width:1000px; margin:0 auto; padding:0 16px 80px; }
header { background:#fff; border-bottom:1px solid #e4e7eb; padding:48px 0 36px; margin-bottom:32px; }
header .wrap { padding-bottom:0; }
h1 { font-size:30px; margin:0 0 10px; letter-spacing:-.4px; }
.sub { color:#6b7280; margin:0; font-size:15px; }
.stats { display:flex; flex-wrap:wrap; gap:28px; margin-top:28px; }
.stat b { display:block; font-size:27px; font-weight:650; letter-spacing:-.5px; }
.stat span { color:#6b7280; font-size:13px; }
section { background:#fff; border:1px solid #e4e7eb; border-radius:12px;
  padding:28px 28px 20px; margin-bottom:24px; }
h2 { font-size:21px; margin:0 0 12px; letter-spacing:-.2px; }
.lead { color:#3e4c59; margin:0 0 18px; font-size:15px; }
.idea { background:#f4f7fe; border-left:3px solid #2f6fed; border-radius:0 8px 8px 0;
  padding:14px 18px; margin:18px 0 0; font-size:14.5px; color:#3e4c59; }
.idea::before { content:"💡 可以做什么"; display:block; font-weight:650; color:#2f6fed;
  font-size:12px; letter-spacing:.06em; margin-bottom:6px; }
code { background:#eef1f5; padding:1px 6px; border-radius:4px; font-size:13px; }
.note { background:#fff8ec; border:1px solid #f0dcb8; border-radius:10px; padding:18px 22px;
  margin-bottom:24px; font-size:14.5px; }
.note h3 { margin:0 0 8px; font-size:15px; }
footer { color:#6b7280; font-size:13px; text-align:center; padding-top:16px; }
a { color:#2f6fed; }
@media (max-width:640px){ section{padding:20px 16px 14px} h1{font-size:24px} .stats{gap:18px} }
"""


def main(out: Path) -> int:
    settings = Settings()
    with connect(settings) as conn:
        stats = headline(conn)
        built = []
        for i, builder in enumerate(SECTIONS):
            print(f"  [{i + 1}/{len(SECTIONS)}] {builder.__name__}")
            built.append(builder(conn))

    parts = [
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>ScholarScope 数据概览</title><style>", CSS, "</style></head><body>",
        "<header><div class='wrap'><h1>这份数据里有什么</h1>",
        "<p class='sub'>RAG 研究语料的一次快速巡视 &middot; 采集于 2026-09-22 &middot; "
        "每个数字都来自数据库的一次 SQL 查询</p><div class='stats'>",
    ]
    parts += [f"<div class='stat'><b>{v}</b><span>{k}</span></div>" for v, k in stats]
    parts += [
        "</div></div></header><div class='wrap'>",
        "<div class='note'><h3>这份报告怎么用</h3>",
        "每一节下面蓝色的「可以做什么」是<strong>给大家起个头的问题，不是任务分配</strong>——"
        "看到感兴趣的就在群里说，或者在 GitHub 上开个 issue。<br><br>"
        "报告里的图是<strong>探索用的，不是最终成品</strong>。真正进 PPT 的图需要各自写脚本从数据库里重新生成，"
        "因为课程要求「必须用 SQL 获取分析数据」。<br><br>"
        "带 ⚠️ 的地方是这份数据已知的坑，完整清单见 "
        "<a href='https://github.com/XIRANZHANG-X/scholarscope-ai/blob/main/docs/%E6%95%B0%E6%8D%AE%E5%AD%97%E5%85%B8.md'>数据字典</a>。</div>",
    ]
    for i, (title, fig, lead, idea) in enumerate(built):
        html = pio.to_html(fig, include_plotlyjs=(i == 0), full_html=False,
                           config={"displayModeBar": False, "responsive": True})
        parts.append(f"<section><h2>{title}</h2><p class='lead'>{lead}</p>{html}"
                     f"<div class='idea'>{idea}</div></section>")
    parts += [
        "<footer>数据源：OpenAlex (CC0)、ROR (CC0)、World Bank (CC BY 4.0)<br>"
        "用 <code>scripts/data_overview.py</code> 生成，数据更新后重跑即可</footer></div></body></html>"
    ]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    print(f"\n{out}  ({out.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/export/数据概览.html")))
