"""Interactive (Plotly) versions of the final figures, for the website.

Each function takes the query results (pandas DataFrames, exactly what the figure scripts get
from the same sql/*.sql files) and returns a plotly Figure. Colours and encodings follow
analysis/common.py, so the page and the slides read the same: RAG / preprint blue, LLM /
conference orange, Agents / article green, foundation models / review yellow; 2026* (to
22 Sep) is drawn dashed with a hollow marker; the highest and lowest values get a black outline.

Titles are not drawn inside the charts: the page sets them as headings above each chart.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from matplotlib.colors import to_hex
from scipy import stats
from scipy.cluster.hierarchy import leaves_list, linkage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(ROOT / "analysis" / "03_world_collaboration"))
import common as C  # noqa: E402
import worldmap  # noqa: E402

PARTIAL = C.PARTIAL_YEAR
FONT = '"Segoe UI Variable Text", "Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif'
INK, INK_2, NEUTRAL, LIGHT, LAND = C.INK, C.INK_2, C.NEUTRAL, C.LIGHT, C.LAND
SEQ = [to_hex(C.SEQUENTIAL(v)) for v in np.linspace(0, 1, 9)]
SEQ_SCALE = [[i / 8, c] for i, c in enumerate(SEQ)]
DIV_SCALE = [[0, C.DOWN], [0.5, C.LAND], [1, C.UP]]
TYPES = ["preprint", "conference-paper", "article", "review"]
MAIN_TYPES = ["preprint", "conference-paper", "article"]
SHORT = {"Hong Kong SAR, China": "Hong Kong", "Macao SAR, China": "Macao", "Korea, Rep.": "South Korea",
         "Iran, Islamic Rep.": "Iran", "Egypt, Arab Rep.": "Egypt", "Viet Nam": "Vietnam",
         "Syrian Arab Republic": "Syria", "Yemen, Rep.": "Yemen", "Turkiye": "Türkiye",
         "Russian Federation": "Russia"}


def short(name: str) -> str:
    return SHORT.get(name, name)


def year_label(y) -> str:
    return f"{int(y)}*" if int(y) == PARTIAL else str(int(y))


def tint(color: str, amount: float) -> str:
    """The colour mixed with white."""
    from matplotlib.colors import to_rgb
    r, g, b = to_rgb(color)
    return to_hex((r + (1 - r) * amount, g + (1 - g) * amount, b + (1 - b) * amount))


def layout(fig: go.Figure, height: int = 440, legend: bool = False, **kw) -> go.Figure:
    axis = dict(showgrid=False, zeroline=False, showline=True, linecolor=INK_2, linewidth=1, ticks="",
                tickfont=dict(size=13, color=INK_2), title=dict(font=dict(size=13, color=INK)),
                automargin=True)
    fig.update_layout(
        height=height, font=dict(family=FONT, size=13, color=INK),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=16, t=16, b=8), showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=13), traceorder="normal"),
        hoverlabel=dict(font=dict(family=FONT, size=13), bgcolor="white", bordercolor=INK_2),
        xaxis=axis, yaxis=axis,
    )
    fig.update_layout(**kw)
    return fig


def partial_line(fig, x, y, color, name, width=3.0, symbol="circle", hover="%{y:,.0f}", group=None,
                 showlegend=False):
    """A line whose last segment (into 2026*) is dashed and whose 2026* marker is hollow."""
    x, y = list(x), list(y)
    solid = [i for i, xi in enumerate(x) if xi < PARTIAL]
    part = [i for i, xi in enumerate(x) if xi >= PARTIAL]
    group = group or name
    tmpl = f"<b>{name}</b> %{{x}}: {hover}<extra></extra>"
    fig.add_scatter(x=[x[i] for i in solid], y=[y[i] for i in solid], mode="lines+markers", name=name,
                    legendgroup=group, showlegend=showlegend, hovertemplate=tmpl,
                    line=dict(color=color, width=width), marker=dict(symbol=symbol, size=8, color=color,
                                                                     line=dict(color="white", width=1.5)))
    if part:
        idx = ([solid[-1]] if solid else []) + part
        fig.add_scatter(x=[x[i] for i in idx], y=[y[i] for i in idx], mode="lines", legendgroup=group,
                        showlegend=False, hoverinfo="skip", line=dict(color=color, width=width, dash="dash"))
        fig.add_scatter(x=[x[i] for i in part], y=[y[i] for i in part], mode="markers", legendgroup=group,
                        showlegend=False, hovertemplate=tmpl.replace("%{x}", "%{x} (to 22 Sep)"),
                        marker=dict(symbol=f"{symbol}-open", size=9, color=color, line=dict(width=2.5)))


def end_labels(fig, x, entries, lo, hi, log=False, gap=0.065):
    """Labels at the right end of lines (paper y coordinates), pushed apart so none overlap.
    `entries` = [(y, text, colour)], `lo`/`hi` the y-axis range in data units."""
    def frac(v):
        if log:
            return (np.log10(v) - np.log10(lo)) / (np.log10(hi) - np.log10(lo))
        return (v - lo) / (hi - lo)
    items = sorted(((frac(y), text, col) for y, text, col in entries), key=lambda t: t[0])
    pos = [p for p, _, _ in items]
    for _ in range(50):                                   # simple relaxation: push neighbours apart
        for i in range(1, len(pos)):
            if pos[i] - pos[i - 1] < gap:
                mid = (pos[i] + pos[i - 1]) / 2
                pos[i - 1], pos[i] = mid - gap / 2, mid + gap / 2
    for p, (_, text, col) in zip(pos, items):
        fig.add_annotation(x=x, y=p, yref="paper", xanchor="left", xshift=12, text=f"<b>{text}</b>",
                           showarrow=False, font=dict(size=13, color=col))


def outline(color=INK, width=3):
    return dict(color=color, width=width)


# ---- direction 1: themes -------------------------------------------------------------------------
def d1_theme_yearly(df):
    fig = go.Figure()
    years = sorted(df["year"].unique())
    ends = []
    for theme, label in C.THEME_LABELS.items():
        d = df[df["theme"] == theme].sort_values("year")
        partial_line(fig, d["year"], d["works_cs"], C.COLORS[theme], label, width=4 if theme == "rag" else 3)
        ends.append((d["works_cs"].iloc[-1], f"{label} {d['works_cs'].iloc[-1]:,.0f}", C.COLORS[theme]))
    lo, hi = 1, 400_000
    end_labels(fig, years[-1], ends, lo, hi, log=True)
    layout(fig, hovermode="x unified", margin=dict(l=8, r=150, t=16, b=8))
    fig.update_xaxes(tickvals=years, ticktext=[year_label(y) for y in years], title_text="Year (2026* to 22 Sep)")
    fig.update_yaxes(type="log", range=[np.log10(lo), np.log10(hi)], tickformat=",", title_text="Papers per year (log scale)")
    return fig


def _growth(df):
    return df.pivot(index="year", columns="theme", values="growth_multiple")[list(C.THEME_LABELS)]


def d1_growth_multiple(df):
    g = _growth(df)
    fig = go.Figure()
    for theme, label in C.THEME_LABELS.items():
        line = [outline() if v in (g.loc[y].max(), g.loc[y].min()) else dict(width=0) for y, v in g[theme].items()]
        fig.add_bar(x=[str(y) for y in g.index], y=g[theme], name=label, marker_color=C.COLORS[theme],
                    marker_line=dict(color=[INK if l["width"] else "rgba(0,0,0,0)" for l in line],
                                     width=[3 if l["width"] else 0 for l in line]),
                    text=[f"{v:.1f}×" for v in g[theme]], textposition="outside", textfont=dict(size=12),
                    hovertemplate=f"<b>{label}</b> %{{x}}: %{{y:.2f}}× the year before<extra></extra>")
    layout(fig, legend=True, barmode="group", bargap=0.25)
    fig.update_xaxes(title_text="Year (each bar: papers that year ÷ papers the year before)")
    fig.update_yaxes(title_text="Growth multiple (×)", range=[0, g.max().max() * 1.18])
    return fig


def d1_growth_heatmap(df):
    g = _growth(df).T
    fig = go.Figure(go.Heatmap(
        z=g.values, x=[str(y) for y in g.columns], y=[C.THEME_LABELS[t] for t in g.index],
        colorscale=SEQ_SCALE, showscale=False, xgap=3, ygap=3,
        text=[[f"{v:.1f}×" for v in row] for row in g.values], texttemplate="<b>%{text}</b>",
        textfont=dict(size=15), hovertemplate="<b>%{y}</b> %{x}: %{z:.2f}×<extra></extra>"))
    layout(fig, height=360)
    fig.update_xaxes(showline=False, side="top")
    fig.update_yaxes(showline=False, autorange="reversed")
    return fig


def d1_growth_by_theme(df):
    g = _growth(df)
    fig = go.Figure()
    ends = []
    for theme, label in C.THEME_LABELS.items():
        col = C.COLORS[theme]
        fig.add_scatter(x=g.index, y=g[theme], mode="lines+markers", name=label,
                        line=dict(color=col, width=4 if theme == "rag" else 3),
                        marker=dict(size=9, color=col, line=dict(color="white", width=1.5)),
                        hovertemplate=f"<b>{label}</b> %{{x}}: %{{y:.2f}}×<extra></extra>")
        peak = g[theme].idxmax()
        fig.add_scatter(x=[peak], y=[g.loc[peak, theme]], mode="markers", hoverinfo="skip", showlegend=False,
                        marker=dict(size=20, color="rgba(0,0,0,0)", line=dict(color=INK, width=2.5)))
        ends.append((g[theme].iloc[-1], f"{label}: peak {g.loc[peak, theme]:.1f}× ({peak})", col))
    hi = g.max().max() * 1.12
    end_labels(fig, g.index[-1], ends, 0, hi, gap=0.07)
    layout(fig, hovermode="x unified", margin=dict(l=8, r=210, t=16, b=8))
    fig.update_xaxes(tickvals=list(g.index), title_text="Year")
    fig.update_yaxes(range=[0, hi], title_text="Papers vs. the year before (×)")
    return fig


def d1_phrase_yearly(df):
    k = df.pivot(index="year", columns="query_key", values="works_cs").fillna(0)
    series = [(k["rag.retrieval_augmented_generation"], "RAG", "rag", "circle"),
              (k["llm.large_language_model"], "LLM", "llm", "diamond"),
              (k["agents.ai_agent"] + k["agents.llm_agent"], "AI agent + LLM agent", "agents", "square"),
              (k["agents.agentic_ai"], "Agentic AI", "agents", "x"),
              (k["fm.pretrained_language_model"], "PLM", "foundation_models", "triangle-up")]
    fig = go.Figure()
    ends = []
    for values, name, theme, symbol in series:
        values = values[values > 0]
        partial_line(fig, values.index, values.values, C.COLORS[theme], name, symbol=symbol,
                     width=4 if theme == "rag" else 3)
        ends.append((values.iloc[-1], f"{name} {values.iloc[-1]:,.0f}", C.COLORS[theme]))
    lo, hi = 0.7, 200_000
    end_labels(fig, k.index[-1], ends, lo, hi, log=True)
    layout(fig, hovermode="x unified", margin=dict(l=8, r=190, t=16, b=8))
    fig.update_xaxes(tickvals=list(k.index), ticktext=[year_label(y) for y in k.index], title_text="Year (2026* to 22 Sep)")
    fig.update_yaxes(type="log", range=[np.log10(lo), np.log10(hi)], tickformat=",", title_text="Papers per year (log scale)")
    return fig


def d1_rag_per_100_llm(df):
    fig = go.Figure()
    names = {"cs": ("Computer science", C.BLUE), "all": ("All fields", C.NEUTRAL)}
    for scope, (name, col) in names.items():
        d = df[df["field_scope"] == scope].sort_values("year")
        if d.empty:
            continue
        partial_line(fig, d["year"], d["rag_per_100_llm"], col, name, hover="%{y:.1f} RAG per 100 LLM papers",
                     showlegend=True)
    years = sorted(df["year"].unique())
    layout(fig, legend=True, hovermode="x unified")
    fig.update_xaxes(tickvals=years, ticktext=[year_label(y) for y in years], title_text="Year (2026* to 22 Sep)")
    fig.update_yaxes(rangemode="tozero", title_text="RAG papers per 100 LLM papers")
    return fig


# ---- direction 2: publication types --------------------------------------------------------------
def d2_type_by_quarter(df):
    w = df.pivot(index="quarter", columns="type", values="works").fillna(0).sort_index()
    fig = go.Figure()
    for t in TYPES:
        fig.add_bar(x=list(w.index), y=w[t], name=C.TYPE_LABELS[t], marker_color=C.COLORS[t],
                    marker_line=dict(color="white", width=1),
                    hovertemplate=f"<b>{C.TYPE_LABELS[t]}</b> %{{x}}: %{{y:,}}<extra></extra>")
    totals = w[TYPES].sum(axis=1)
    fig.add_scatter(x=list(w.index), y=totals, mode="text", text=[f"<b>{v:,.0f}</b>" for v in totals],
                    textposition="top center", hoverinfo="skip", showlegend=False, textfont=dict(size=12))
    fig.add_vrect(x0=len(w) - 2.5, x1=len(w) - 0.5, fillcolor=LAND, line_width=0, layer="below",
                  annotation_text="<b>Incomplete*</b>", annotation_position="top")
    layout(fig, legend=True, barmode="stack", bargap=0.3)
    fig.update_xaxes(title_text="Quarter (papers dated to the year only are left out)")
    fig.update_yaxes(tickformat=",", range=[0, totals.max() * 1.18], title_text="Papers")
    return fig


def _share_bars(rows, shares, height=300):
    fig = go.Figure()
    for t in TYPES:
        vals = shares[t]
        fig.add_bar(y=rows, x=vals, orientation="h", name=C.TYPE_LABELS[t], marker_color=C.COLORS[t],
                    marker_line=dict(color="white", width=1.5),
                    text=[f"<b>{v:.0f}%</b>" if v >= 7 else "" for v in vals], textposition="inside",
                    insidetextanchor="middle", textfont=dict(color="white" if t != "review" else INK, size=13),
                    hovertemplate=f"<b>{C.TYPE_LABELS[t]}</b> · %{{y}}: %{{x:.1f}}%<extra></extra>")
    layout(fig, height=height, legend=True, barmode="stack", bargap=0.35)
    fig.update_xaxes(range=[0, 100], ticksuffix="%", title_text="Share of papers")
    fig.update_yaxes(autorange="reversed", showline=False)
    return fig


def d2_type_share_by_year(df):
    t = df.pivot(index="year", columns="type", values="share_pct").fillna(0)
    years = [2024, 2025, 2026]
    return _share_bars([year_label(y) for y in years], {k: t.loc[years, k].tolist() for k in TYPES})


def d2_type_mix_by_theme(df):
    t = df.pivot(index="theme", columns="type", values="share_pct").fillna(0)
    themes = list(C.THEME_LABELS)
    return _share_bars([C.THEME_LABELS[x] for x in themes], {k: t.loc[themes, k].tolist() for k in TYPES}, 340)


def _cohort_bars(df, column, fmt, ylabel, ymax, suffix=""):
    df = df[df["type"].isin(MAIN_TYPES)]
    n = df.pivot(index="type", columns="cohort_year", values="works")
    fig = go.Figure()
    for year, amount in ((2024, 0.0), (2025, 0.55)):
        d = df[df["cohort_year"] == year].set_index("type").loc[MAIN_TYPES]
        fig.add_bar(x=[f"{C.TYPE_LABELS[t]}<br>(n = {n.loc[t, 2024]:,} / {n.loc[t, 2025]:,})" for t in MAIN_TYPES],
                    y=d[column], name=f"Published in {year}",
                    marker_color=[tint(C.COLORS[t], amount) for t in MAIN_TYPES],
                    text=[fmt(v) for v in d[column]], textposition="outside", textfont=dict(size=13),
                    hovertemplate=f"%{{x}}<br>Published in {year}: %{{y:.1f}}{suffix}<extra></extra>")
    layout(fig, barmode="group", bargap=0.3, bargroupgap=0.06, legend=False, margin=dict(l=8, r=16, t=40, b=8))
    fig.add_annotation(x=0, y=1.1, xref="paper", yref="paper", xanchor="left", showarrow=False,
                       text="Full colour: published in 2024 · light: published in 2025", font=dict(size=13, color=INK_2))
    fig.update_xaxes(title_text="Publication type (papers published in 2024 / 2025)")
    fig.update_yaxes(range=[0, ymax * 1.15], ticksuffix=suffix, title_text=f"{ylabel} (to 22 Sep 2026)")
    return fig


def d2_cited_share(df):
    return _cohort_bars(df, "cited_share_pct", lambda v: f"<b>{v:.0f}%</b>", "Share of papers cited", 100, "%")


def d2_mean_citations(df):
    return _cohort_bars(df, "mean_citations", lambda v: f"<b>{v:.1f}</b>", "Mean citations per paper",
                        df["mean_citations"].max())


def _venue_name(name: str) -> str:
    import re
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).rstrip(". ")
    if name.isupper() and " " in name:
        name = name.title()
    return name


def d2_sources(df, key):
    d = df[df["type"] == key].sort_values("rank_in_type").head(5)
    missing = int(d["works_without_source"].iloc[0])
    total = int(d["works_in_type"].iloc[0])
    names = [_venue_name(n) for n in d["source_name"]] + ["No venue recorded"]
    values = list(d["works"]) + [missing]
    fig = go.Figure(go.Bar(
        y=names, x=values, orientation="h", marker_color=[C.COLORS[key]] * len(d) + [NEUTRAL],
        text=[f"<b>{v:,}</b> ({v / total:.0%})" for v in values], textposition="outside",
        hovertemplate="<b>%{y}</b>: %{x:,} papers<extra></extra>", cliponaxis=False))
    layout(fig, height=360, bargap=0.35, margin=dict(l=8, r=90, t=16, b=8))
    fig.update_yaxes(autorange="reversed", showline=True)
    fig.update_xaxes(tickformat=",", range=[0, max(values) * 1.2],
                     title_text=f"Papers (of {total:,} {C.TYPE_LABELS[key].lower()}s)")
    return fig


# ---- direction 3: geography ----------------------------------------------------------------------
_RINGS = None


def _rings():
    """Projected country outlines, rounded to keep the page small: {ISO-2: [(xs, ys), ...]}."""
    global _RINGS
    if _RINGS is None:
        _RINGS = {}
        for code, ring in worldmap._outlines():
            if worldmap._area(ring) < 2e-5:            # specks add bytes, not information
                continue
            _RINGS.setdefault(code, []).append((np.round(ring[:, 0], 3), np.round(ring[:, 1], 3)))
    return _RINGS


def _joined(rings):
    xs, ys = [], []
    for x, y in rings:
        xs += x.tolist() + [None]
        ys += y.tolist() + [None]
    return xs, ys


def _map_layout(fig, height=480):
    layout(fig, height=height, margin=dict(l=0, r=0, t=0, b=0))
    fig.update_xaxes(visible=False, range=[-2.75, 2.75])
    fig.update_yaxes(visible=False, scaleanchor="x", range=[-1.15, 1.4])
    return fig


def d3_world_map(df):
    papers = df.set_index("country_code")
    lo, hi = np.log10(1), np.log10(papers["papers"].max())
    fig = go.Figure()
    rings = _rings()
    for code, rs in rings.items():
        xs, ys = _joined(rs)
        if code in papers.index:
            n = int(papers.loc[code, "papers"])
            col = to_hex(C.SEQUENTIAL(0.12 + 0.88 * (np.log10(n) - lo) / (hi - lo)))
            name = f"{short(papers.loc[code, 'country'])}: {n:,} papers"
        else:
            col, name = LAND, "No RAG paper attributed"
        fig.add_scatter(x=xs, y=ys, fill="toself", fillcolor=col, mode="lines", name=name,
                        line=dict(color="white", width=0.5), hoveron="fills", hoverinfo="name", showlegend=False)
    small = papers[~papers.index.isin(rings.keys())].dropna(subset=["latitude", "longitude"])
    sx, sy = worldmap.project(small["longitude"], small["latitude"])
    fig.add_scatter(x=sx, y=sy, mode="markers", showlegend=False,
                    marker=dict(size=8, color=[to_hex(C.SEQUENTIAL(0.12 + 0.88 * (np.log10(n) - lo) / (hi - lo)))
                                               for n in small["papers"]], line=dict(color=INK, width=1)),
                    text=[f"{short(c)}: {n:,} papers" for c, n in zip(small["country"], small["papers"])],
                    hovertemplate="%{text}<extra></extra>")
    # colour key: an invisible trace that only carries the colour bar (log ticks)
    ticks = [v for v in (1, 10, 100, 1000) if v <= 10 ** hi]
    fig.add_scatter(x=[None], y=[None], mode="markers", showlegend=False, hoverinfo="skip",
                    marker=dict(colorscale=[[0, to_hex(C.SEQUENTIAL(0.12))], [1, to_hex(C.SEQUENTIAL(1.0))]],
                                cmin=lo, cmax=hi, color=[lo], showscale=True,
                                colorbar=dict(title=dict(text="Papers", side="top"), orientation="h", x=0.02,
                                              xanchor="left", y=0.02, len=0.3, thickness=10,
                                              tickvals=np.log10(ticks), ticktext=[f"{v:,}" for v in ticks],
                                              outlinewidth=0)))
    return _map_layout(fig)


def d3_top_countries(df):
    df = df.sort_values("papers", ascending=False)
    names = [short(n) for n in df["country"]]
    fig = go.Figure()
    fig.add_bar(y=names, x=df["domestic_only"], orientation="h", name="Authors in one country",
                marker_color=C.BLUE, hovertemplate="<b>%{y}</b>: %{x:,} with authors in one country<extra></extra>")
    fig.add_bar(y=names, x=df["international"], orientation="h", name="Co-authored across countries",
                marker_color=C.ORANGE, hovertemplate="<b>%{y}</b>: %{x:,} co-authored across countries<extra></extra>")
    fig.add_scatter(y=names, x=df["papers"], mode="text", text=[f"  <b>{v:,}</b>" for v in df["papers"]],
                    textposition="middle right", hoverinfo="skip", showlegend=False)
    layout(fig, height=520, legend=True, barmode="stack", bargap=0.3)
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(tickformat=",", range=[0, df["papers"].max() * 1.15], title_text="RAG papers with a known country")
    return fig


def d3_international_share(df):
    df = df.sort_values("international_share_pct", ascending=False)
    v = df["international_share_pct"].to_numpy()
    n = len(v)
    fig = go.Figure(go.Bar(
        y=[short(c) for c in df["country"]], x=v, orientation="h", marker_color=C.NATURE[:n],
        marker_line=dict(color=[INK if i in (0, n - 1) else "rgba(0,0,0,0)" for i in range(n)],
                         width=[3 if i in (0, n - 1) else 0 for i in range(n)]),
        text=[f"<b>{x:.1f}%</b>" + ("  Highest" if i == 0 else "  Lowest" if i == n - 1 else "")
              for i, x in enumerate(v)], textposition="outside", cliponaxis=False,
        customdata=df["papers"], hovertemplate="<b>%{y}</b>: %{x:.1f}% of %{customdata:,} papers<extra></extra>"))
    layout(fig, height=520, bargap=0.3)
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(range=[0, 115], tickvals=[0, 25, 50, 75, 100], ticktext=["0%", "25%", "50%", "75%", "100%"],
                     title_text="Share of the country’s papers co-authored with another country")
    return fig


def d3_country_share_by_year(df):
    years = sorted(df["year"].unique())
    totals = df.drop_duplicates("year").set_index("year")["papers_with_country"]
    fig = go.Figure()
    ends = []
    for i, (code, d) in enumerate(df.sort_values("overall_rank").groupby("country_code", sort=False)):
        d = d.sort_values("year")
        col = C.SERIES[i % len(C.SERIES)]
        name = short(d["country"].iloc[0])
        partial_line(fig, d["year"], d["share_pct"], col, name, hover="%{y:.1f}%")
        ends.append((d["share_pct"].iloc[-1], f"{name} {d['share_pct'].iloc[-1]:.0f}%", col))
    end_labels(fig, years[-1], ends, 0, 60)
    layout(fig, hovermode="x unified", margin=dict(l=8, r=170, t=16, b=8))
    fig.update_xaxes(tickvals=years, ticktext=[f"{year_label(y)}<br>n = {totals[y]:,}" for y in years],
                     title_text="Year (n = papers with a known country; 2026* to 22 Sep)")
    fig.update_yaxes(range=[0, 60], ticksuffix="%", title_text="Share of the year’s papers")
    return fig


def d3_collab_matrix(df):
    order = df.drop_duplicates("country_code_a").sort_values("rank_a")
    codes, names = list(order["country_code_a"]), [short(n) for n in order["country_a"]]
    m = df.pivot(index="country_code_a", columns="country_code_b", values="share_of_a_pct").reindex(index=codes, columns=codes)
    j = df.pivot(index="country_code_a", columns="country_code_b", values="joint_papers").reindex(index=codes, columns=codes)
    text = [[f"{v:.0f}" if pd.notna(v) and v >= 1 else "" for v in row] for row in m.values]
    fig = go.Figure(go.Heatmap(
        z=m.values, x=names, y=names, colorscale=SEQ_SCALE, xgap=2, ygap=2, text=text,
        texttemplate="%{text}", textfont=dict(size=11), customdata=j.values,
        hovertemplate="<b>%{y}</b> with <b>%{x}</b>: %{customdata:,} joint papers (%{z:.1f}% of %{y}’s)<extra></extra>",
        colorbar=dict(title=dict(text="% of row country’s papers", side="right"), thickness=10, outlinewidth=0)))
    layout(fig, height=560)
    fig.update_xaxes(showline=False, side="top", tickangle=-40)
    fig.update_yaxes(showline=False, autorange="reversed")
    return fig


def d3_collab_map(df):
    df = df.sort_values("rank")
    fig = go.Figure()
    xs, ys = _joined([r for rs in _rings().values() for r in rs])
    fig.add_scatter(x=xs, y=ys, fill="toself", fillcolor=LAND, mode="lines", hoverinfo="skip",
                    line=dict(color="white", width=0.5), showlegend=False)
    top = df["joint_papers"].max()
    for i, r in enumerate(df.itertuples()):
        (x0, x1), (y0, y1) = worldmap.project([r.longitude_a, r.longitude_b], [r.latitude_a, r.latitude_b])
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = x1 - x0, y1 - y0
        bend = 0.18 * np.hypot(dx, dy)
        cx, cy = mx - dy / max(np.hypot(dx, dy), 1e-9) * bend, my + dx / max(np.hypot(dx, dy), 1e-9) * bend
        t = np.linspace(0, 1, 24)
        bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1
        by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1
        top5 = i < 5
        fig.add_scatter(x=bx, y=by, mode="lines", showlegend=False,
                        line=dict(color=C.DARK_BLUE if top5 else C.LIGHT_BLUE,
                                  width=1.5 + 7 * r.joint_papers / top),
                        name=f"{short(r.country_a)} – {short(r.country_b)}: {r.joint_papers:,} joint papers",
                        hoverinfo="name", opacity=0.95 if top5 else 0.8)
    ends = pd.concat([df[["country_a", "latitude_a", "longitude_a"]].set_axis(["c", "lat", "lon"], axis=1),
                      df[["country_b", "latitude_b", "longitude_b"]].set_axis(["c", "lat", "lon"], axis=1)]).drop_duplicates("c")
    ex, ey = worldmap.project(ends["lon"], ends["lat"])
    fig.add_scatter(x=ex, y=ey, mode="markers", showlegend=False, text=[short(c) for c in ends["c"]],
                    hovertemplate="%{text}<extra></extra>", marker=dict(size=6, color=INK))
    return _map_layout(fig)


def d3_coverage_by_type(df):
    df = df.sort_values("with_country_pct", ascending=False)
    names = [f"{C.TYPE_LABELS[t]}<br>({w:,} of {n:,})" for t, w, n in zip(df["type"], df["with_country"], df["works"])]
    fig = go.Figure()
    fig.add_bar(y=names, x=df["with_country_pct"], orientation="h", name="Country identified", marker_color=C.BLUE,
                text=[f"<b>{v:.0f}%</b>" for v in df["with_country_pct"]], textposition="inside",
                insidetextanchor="middle", textfont=dict(color="white"),
                hovertemplate="%{y}: %{x:.1f}% with a country<extra></extra>")
    fig.add_bar(y=names, x=100 - df["with_country_pct"], orientation="h", name="No country identified",
                marker_color=LIGHT, hoverinfo="skip")
    layout(fig, height=360, legend=True, barmode="stack", bargap=0.35)
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(range=[0, 100], ticksuffix="%", title_text="Share of papers")
    return fig


def d3_coverage_by_year(df):
    df = df.sort_values("year")
    partial = df["year"] == PARTIAL
    fig = go.Figure(go.Bar(
        x=[f"{year_label(y)}<br>n = {n:,}" for y, n in zip(df["year"], df["works"])], y=df["with_country_pct"],
        marker=dict(color=[ "rgba(0,0,0,0)" if p else C.BLUE for p in partial],
                    line=dict(color=C.BLUE, width=[2.5 if p else 0 for p in partial]),
                    pattern=dict(shape=["/" if p else "" for p in partial], fgcolor=C.BLUE)),
        text=[f"<b>{v:.0f}%</b>" for v in df["with_country_pct"]], textposition="outside",
        hovertemplate="%{x}: %{y:.1f}% with a known country<extra></extra>"))
    layout(fig, bargap=0.45)
    fig.update_xaxes(title_text="Year (n = all papers; 2026* to 22 Sep)")
    fig.update_yaxes(range=[0, 100], ticksuffix="%", title_text="Papers with a known country")
    return fig


# ---- direction 4: sub-directions -----------------------------------------------------------------
def d4_sizes(df, kind):
    d = df[df["kind"] == kind].sort_values("papers", ascending=False)
    fig = go.Figure()
    fig.add_bar(y=d["label"], x=d["papers"], orientation="h", name="Mentioned in title or abstract",
                marker_color=C.LIGHT_BLUE, width=0.7,
                text=[f"<b>{p:,}</b> ({s:.0f}%)" for p, s in zip(d["papers"], d["share_pct"])],
                textposition="outside", cliponaxis=False,
                hovertemplate="<b>%{y}</b>: %{x:,} mention it in title or abstract<extra></extra>")
    fig.add_bar(y=d["label"], x=d["papers_title"], orientation="h", name="Named in the title",
                marker_color=C.DARK_BLUE, width=0.34, text=[f"<b>{v:,}</b>" for v in d["papers_title"]],
                textposition="inside", insidetextanchor="end", textfont=dict(color="white"),
                hovertemplate="<b>%{y}</b>: %{x:,} name it in the title<extra></extra>")
    layout(fig, height=max(300, 56 * len(d)), legend=True, barmode="overlay")
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(tickformat=",", range=[0, df["papers"].max() * 1.22], title_text="RAG papers with an abstract, 2019–2026")
    return fig


def d4_share_shift(df):
    broad = df.pivot(index="label", columns="year", values="share_pct")
    title = df.pivot(index="label", columns="year", values="share_title_pct")
    a, b = 2024, PARTIAL
    change = (broad[b] - broad[a]).sort_values()
    fig = go.Figure()
    for name, delta in change.items():
        col = C.UP if delta > 0.5 else C.DOWN if delta < -0.5 else NEUTRAL
        x0, x1 = broad.loc[name, a], broad.loc[name, b]
        fig.add_scatter(x=[x0, x1], y=[name, name], mode="lines", line=dict(color=tint(col, 0.5), width=6),
                        hoverinfo="skip", showlegend=False)
        fig.add_scatter(x=[x0], y=[name], mode="markers", showlegend=False,
                        marker=dict(size=13, color="white", line=dict(color=col, width=3)),
                        hovertemplate=f"<b>{name}</b> 2024: %{{x:.1f}}%<extra></extra>")
        fig.add_scatter(x=[x1], y=[name], mode="markers+text", showlegend=False,
                        marker=dict(size=13, color=col, line=dict(color="white", width=1.5)),
                        text=[f"  <b>{x1:.0f}%</b> ({delta:+.0f} pp)"],
                        textposition="middle right" if x1 >= x0 else "middle left",
                        textfont=dict(color=col),
                        hovertemplate=(f"<b>{name}</b> 2026*: %{{x:.1f}}%<br>change {delta:+.1f} pp; "
                                       f"named in the title {title.loc[name, a]:.1f}% → {title.loc[name, b]:.1f}%<extra></extra>"))
    layout(fig, height=560)
    fig.add_annotation(x=1, y=0, xref="paper", yref="paper", xanchor="right", yanchor="bottom", showarrow=False,
                       text="○ 2024   ● 2026* (to 22 Sep)", font=dict(size=13, color=INK_2))
    fig.update_xaxes(range=[0, broad[[a, b]].max().max() * 1.3], ticksuffix="%",
                     title_text="Share of the year’s RAG papers (mentioned in title or abstract)")
    return fig


def d4_cooccurrence(df):
    labels = df.drop_duplicates("subfield_a").set_index("subfield_a")["label_a"]
    keys = list(labels.index)
    lift = df.pivot(index="subfield_a", columns="subfield_b", values="lift").reindex(index=keys, columns=keys).to_numpy(float)
    with np.errstate(divide="ignore"):
        score = np.clip(np.log2(np.where(np.isnan(lift), 1.0, np.maximum(lift, 0.25))), -2, 2)
    np.fill_diagonal(score, 0.0)
    order = leaves_list(linkage(score, method="average", optimal_ordering=True))
    score, lift = score[np.ix_(order, order)], lift[np.ix_(order, order)]
    names = [labels[keys[i]] for i in order]
    n = len(names)
    mask = np.tril(np.ones((n, n), bool), -1)
    z = np.where(mask, score, np.nan)[1:, :-1]
    lv = np.where(mask, lift, np.nan)[1:, :-1]
    text = [[f"{v:.1f}" if not np.isnan(v) and abs(np.log2(v)) >= np.log2(1.3) else "" for v in row] for row in lv]
    fig = go.Figure(go.Heatmap(
        z=z, x=names[:-1], y=names[1:], zmin=-2, zmax=2, colorscale=DIV_SCALE, xgap=2, ygap=2,
        text=text, texttemplate="%{text}", customdata=lv, textfont=dict(size=12),
        hovertemplate="<b>%{y}</b> × <b>%{x}</b>: %{customdata:.2f}× as often as chance<extra></extra>",
        colorbar=dict(title=dict(text="vs. chance", side="right"), tickvals=[-2, -1, 0, 1, 2],
                      ticktext=["¼×", "½×", "1×", "2×", "4×"], thickness=10, outlinewidth=0)))
    hi = np.unravel_index(np.nanargmax(lv), lv.shape)
    lo = np.unravel_index(np.nanargmin(lv), lv.shape)
    for (i, j) in (hi, lo):
        fig.add_shape(type="rect", x0=j - 0.48, x1=j + 0.48, y0=i - 0.48, y1=i + 0.48, line=dict(color=INK, width=3))
    layout(fig, height=600)
    fig.update_xaxes(showline=False, tickangle=-35)
    fig.update_yaxes(showline=False, autorange="reversed")
    return fig


# ---- direction 5: country indicators -------------------------------------------------------------
X_AXES = {"gdp_pc": "GDP per capita (US$, log scale)", "rd_pct": "R&D expenditure (% of GDP, log scale)",
          "pop_m": "Population (millions, log scale)"}
Y_AXES = {"papers": "RAG papers (log scale)", "papers_per_m": "RAG papers per million people (log scale)"}


def log_ticks(lo, hi):
    """Tick values for a log axis: 1-2-5 steps when it spans few decades, powers of ten otherwise."""
    a, b = int(np.floor(np.log10(lo))), int(np.ceil(np.log10(hi)))
    steps = (1, 2, 5) if b - a <= 3 else (1,)
    vals = [m * 10 ** e for e in range(a, b + 1) for m in steps if lo <= m * 10 ** e <= hi]
    return vals, [f"{v:,.10g}" for v in vals]


def _log_axis(axis_update, lo, hi, **kw):
    vals, text = log_ticks(lo, hi)
    axis_update(type="log", tickvals=vals, ticktext=text, **kw)


def _name_points(fig, xs, ys, names, above, x_all):
    """Labels with short leader lines: above the reference line the label goes up, below it down;
    points in the right third of the axis are labelled to their left."""
    lx_lo, lx_hi = np.log10(np.min(x_all)), np.log10(np.max(x_all))
    ly_all = np.log10(np.asarray(ys, float))
    # Rough pixel scale of a ~900 x 380 px plot area, to keep label boxes from overlapping.
    px_x = 900 / max(lx_hi - lx_lo, 1e-9)
    px_y = 380 / max(ly_all.max() - ly_all.min() + 1.0, 1e-9)
    placed = []                                            # label boxes (x0, y0, x1, y1) in px
    for x, y, name, up in sorted(zip(xs, ys, names, above), key=lambda t: -t[1]):
        text = short(name)
        w, h = 8.5 * len(text), 18
        cx, cy = (np.log10(x) - lx_lo) * px_x, np.log10(y) * px_y
        side = -1 if (np.log10(x) - lx_lo) / (lx_hi - lx_lo) > 0.66 else 1
        vert = 1 if up else -1                              # +1: label above the point
        best = None
        for ax_, ay_ in ((36 * side, 24 * vert), (36 * side, 48 * vert), (-36 * side, 24 * vert),
                         (-36 * side, 48 * vert), (36 * side, -24 * vert), (64 * side, 70 * vert)):
            bx = cx + ax_ - (w / 2)
            by = cy + ay_ - h / 2
            box = (bx, by, bx + w, by + h)
            if not any(box[0] < p[2] and box[2] > p[0] and box[1] < p[3] and box[3] > p[1] for p in placed):
                best = (ax_, ay_, box)
                break
        if best is None:
            best = (36 * side, 24 * vert, None)
        ax_, ay_, box = best
        if box:
            placed.append(box)
        fig.add_annotation(x=np.log10(x), y=np.log10(y), text=f"<b>{text}</b>", showarrow=True,
                           arrowhead=0, arrowwidth=1.2, arrowcolor=INK_2, ax=ax_, ay=-ay_,
                           font=dict(size=13, color=INK))


def d5_trend(sub, ycol, xcol, min_papers):
    lx, ly = np.log(sub[xcol]), np.log(sub[ycol])
    fit = stats.linregress(lx, ly)
    fig = go.Figure()
    fig.add_scatter(x=sub[xcol], y=sub[ycol], mode="markers", name=f"Country (n = {len(sub)}, ≥{min_papers} papers)",
                    marker=dict(size=10, color=NEUTRAL, line=dict(color="white", width=1)),
                    text=[short(n) for n in sub["name"]],
                    hovertemplate="<b>%{text}</b><br>x: %{x:,.3~g}<br>y: %{y:,.3~g}<extra></extra>")
    grid = np.linspace(lx.min(), lx.max(), 50)
    fig.add_scatter(x=np.exp(grid), y=np.exp(fit.intercept + fit.slope * grid), mode="lines", hoverinfo="skip",
                    name=f"Trendline: slope {fit.slope:+.2f}, R² {fit.rvalue ** 2:.2f}", line=dict(color=C.BLUE, width=3))
    resid = ly - (fit.intercept + fit.slope * lx)
    top = resid.abs().nlargest(3).index.union(sub[ycol].nlargest(2).index)
    _name_points(fig, sub.loc[top, xcol], sub.loc[top, ycol], sub.loc[top, "name"], resid[top] > 0, sub[xcol])
    layout(fig, height=480, legend=True)
    _log_axis(fig.update_xaxes, sub[xcol].min(), sub[xcol].max(), title_text=X_AXES[xcol])
    _log_axis(fig.update_yaxes, sub[ycol].min(), sub[ycol].max(), title_text=Y_AXES[ycol])
    return fig


def d5_actual_vs_predicted(t, actual, predicted):
    flagged = t["flag_residual"] | t["flag_influential"]
    fig = go.Figure()
    lo = min(t[predicted].min(), t[actual].min()) * 0.7
    hi = max(t[predicted].max(), t[actual].max()) * 1.4
    fig.add_scatter(x=[lo, hi], y=[lo, hi], mode="lines", name="Actual = predicted", hoverinfo="skip",
                    line=dict(color=INK_2, width=1.5, dash="dash"))
    for mask, col, name in ((~flagged, NEUTRAL, "Within expectation"),
                            (flagged & (t["stud_resid"] > 0), C.UP, "More papers than predicted (flagged)"),
                            (flagged & (t["stud_resid"] <= 0), C.DOWN, "Fewer papers than predicted (flagged)")):
        d = t[mask]
        fig.add_scatter(x=d[predicted], y=d[actual], mode="markers", name=name,
                        marker=dict(size=10, color=col, line=dict(color="white", width=1)),
                        text=[short(c) for c in d["country"]],
                        hovertemplate="<b>%{text}</b><br>predicted %{x:,.3~g} · actual %{y:,.3~g}<extra></extra>")
    f = t[flagged]
    _name_points(fig, f[predicted], f[actual], f["country"], f[actual] > f[predicted], t[predicted])
    layout(fig, height=520, legend=True)
    _log_axis(fig.update_xaxes, lo, hi, range=[np.log10(lo), np.log10(hi)], title_text="Predicted by the model (log scale)")
    _log_axis(fig.update_yaxes, lo, hi, range=[np.log10(lo), np.log10(hi)], title_text="Actual (log scale)")
    return fig


def d5_coefficients(coefs, outcome, terms, term_labels, min_papers):
    models = [("NB, ≥5 papers", f"Negative binomial, ≥{min_papers} papers (main)"),
              ("NB, all countries", "Negative binomial, all countries"),
              ("OLS log, ≥5 papers", f"OLS on log(papers), ≥{min_papers} papers")]
    fig = go.Figure()
    for j, (key, label) in enumerate(models):
        d = coefs[(coefs["model"] == key) & (coefs["outcome"] == outcome)].set_index("term").loc[terms]
        y = np.arange(len(terms)) + (j - 1) * 0.22
        fig.add_scatter(x=d["coef"], y=y, mode="markers", name=label,
                        marker=dict(size=11, color=C.SERIES[j]),
                        error_x=dict(type="data", symmetric=False, array=d["ci_high"] - d["coef"],
                                     arrayminus=d["coef"] - d["ci_low"], color=C.SERIES[j], thickness=3, width=0),
                        customdata=np.column_stack([d["ci_low"], d["ci_high"], d["p_value"]]),
                        text=[term_labels[t] for t in terms],
                        hovertemplate=(f"<b>%{{text}}</b> · {label}<br>elasticity %{{x:.2f}} "
                                       "(95% CI %{customdata[0]:.2f} to %{customdata[1]:.2f}), p = %{customdata[2]:.3f}<extra></extra>"))
    fig.add_vline(x=0, line=dict(color=INK_2, width=1))
    layout(fig, height=120 + 120 * len(terms), legend=True)
    fig.update_yaxes(tickvals=list(range(len(terms))), ticktext=[term_labels[t] for t in terms], autorange="reversed",
                     showline=False)
    fig.update_xaxes(range=[-0.6, 1.5], title_text="Elasticity (95% confidence interval)")
    return fig


def to_json(fig: go.Figure) -> str:
    """Figure as compact JSON for Plotly.newPlot ({"data": ..., "layout": ...})."""
    import plotly.io as pio
    # Escape "</" so the JSON can sit inside a <script> element.
    return pio.to_json(fig, validate=False, pretty=False).replace("</", "<\\/")
