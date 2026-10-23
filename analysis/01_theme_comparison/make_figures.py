"""Direction 1: RAG next to the other large-model research themes.

    uv run python analysis/01_theme_comparison/make_figures.py [--from-csv]

Data: meta.recall_probes (yearly counts OpenAlex returned for eight search phrases; only RAG's
papers were downloaded, the other themes are counts). Highest / lowest values are called out;
growth is shown three ways (grouped bars, heatmap, one line per theme). The house style comes
from common.py. One plot per figure. See README.md for each figure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LogNorm
from matplotlib.ticker import FuncFormatter

HERE = Path(__file__).resolve().parent
if not (HERE / "common.py").exists():       # in the repo the shared module sits one level up;
    sys.path.insert(0, str(HERE.parent))    # a folder handed over on its own carries a copy
import common as C  # noqa: E402

THEMES = list(C.THEME_LABELS)
# Shape follows the theme too, so the lines stay apart in greyscale print and for colour-blind readers.
MARKERS = {"rag": "o", "llm": "D", "agents": "s", "foundation_models": "^"}
PARTIAL = C.PARTIAL_YEAR

thousands = FuncFormatter(lambda v, _: f"{v:,.0f}")
times = FuncFormatter(lambda v, _: f"{v:g}x")


def _extremes(values) -> tuple[set, set]:
    """Positions of the maximum and minimum as shown (one decimal), so labelled ties tie here too."""
    shown = np.round(np.asarray(values, dtype=float), 1)
    return set(np.flatnonzero(shown == shown.max())), set(np.flatnonzero(shown == shown.min()))


def fig1_theme_yearly(data: C.Data) -> None:
    df = data.fetch("fig1_theme_yearly")
    fig, ax = C.new_figure()
    ends = []
    for theme in [t for t in THEMES if t != "rag"] + ["rag"]:          # RAG, the subject, on top
        d = df[df["theme"] == theme].sort_values("year")
        C.line(ax, d["year"], d["works_cs"], C.COLORS[theme], marker=MARKERS[theme],
               zorder=5 if theme == "rag" else 3)
        ends.append((d["works_cs"].iloc[-1], d["theme"].iloc[0], f"{C.THEME_LABELS[theme]} {d['works_cs'].iloc[-1]:,.0f}"))
    for theme in THEMES:                                               # legend in theme order
        ax.plot([], [], color=C.COLORS[theme], marker=MARKERS[theme], markeredgecolor="white",
                label=C.THEME_LABELS[theme])
    last = df[df["year"] == PARTIAL]
    high, low = last.loc[last["works_cs"].idxmax(), "theme"], last.loc[last["works_cs"].idxmin(), "theme"]
    entries = [(y, ("Highest: " if t == high else "Lowest: " if t == low else "") + text) for y, t, text in ends]
    # RAG's steepest one-year step is an interval, so it gets a box rather than a point.
    rag = df[(df["theme"] == "rag") & (df["year"] < PARTIAL)].sort_values("year")
    step = (rag["works_cs"] / rag["works_cs"].shift()).to_numpy()
    k = int(np.nanargmax(step))
    x0, x1 = rag["year"].iloc[k - 1], rag["year"].iloc[k]
    y0, y1 = rag["works_cs"].iloc[k - 1] / 1.6, rag["works_cs"].iloc[k] * 1.6
    ax.add_patch(plt.Rectangle((x0 - 0.25, y0), x1 - x0 + 0.5, y1 - y0, fill=False, edgecolor=C.HIGHLIGHT,
                               lw=3, zorder=6))
    C.label(ax, x1 + 0.3, y0, f"Steepest climb: RAG x{step[k]:.1f}\n({x0:.0f} to {x1:.0f})",
            dx=6, dy=0, ha="left", va="bottom", color=C.HIGHLIGHT)
    ax.set_yscale("log")
    ax.set_ylim(1, 1_000_000)
    ax.yaxis.set_major_formatter(thousands)
    ax.set_xticks(sorted(df["year"].unique()))
    ax.set_xticklabels([f"{y}*" if y == PARTIAL else str(y) for y in sorted(df["year"].unique())])
    ax.set_xlim(2018.6, 2026.3)
    ax.set_xlabel("Year (2026* to 22 Sep)")
    ax.set_ylabel("Papers per year (log scale)")
    C.legend(ax, loc="upper left", ncol=4)
    C.finish(fig, ax, "Papers per Year by Research Theme")
    C.end_labels(ax, PARTIAL, entries)
    data.save(fig, "fig1_theme_yearly")


def fig2_growth_multiple(data: C.Data) -> None:
    df = data.fetch("fig2_growth_multiple")
    df = df[df["year"] >= 2023]
    years = sorted(df["year"].unique())
    fig, ax = C.new_figure()
    width = 0.19
    for j, year in enumerate(years):
        group = df[df["year"] == year].set_index("theme").loc[THEMES, "growth_multiple"]
        hi, lo = _extremes(group)
        for i, (theme, value) in enumerate(group.items()):
            x = j + (i - 1.5) * (width + 0.03)
            bar = ax.bar(x, value - 1, width, bottom=1, color=C.COLORS[theme], zorder=3)[0]
            word = "Highest\n" if i in hi else "Lowest\n" if i in lo else ""
            if word:
                C.highlight_bar(bar)
            C.label(ax, x, value, f"{word}{value:.1f}x", color=C.HIGHLIGHT if word else C.INK_2)
    for theme in THEMES:                       # legend swatches without the highlight outline
        ax.bar([0], [0], color=C.COLORS[theme], label=C.THEME_LABELS[theme])
    ax.axhline(1, color=C.INK_2, lw=1)
    ax.set_ylim(1, df["growth_multiple"].max() * 1.3)
    ax.set_yticks([1, 5, 10, 15, 20])
    ax.yaxis.set_major_formatter(times)
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([f"{y - 1} to {y}" for y in years])
    ax.set_ylabel("Papers vs. previous year")
    C.legend(ax, loc="upper right", ncol=4)
    C.finish(fig, ax, "Year-over-Year Growth by Theme")
    data.save(fig, "fig2_growth_multiple")


def fig2b_growth_heatmap(data: C.Data) -> None:
    df = data.fetch("fig2_growth_multiple")
    df = df[df["year"] >= 2023]
    years = sorted(df["year"].unique())
    grid = np.array([[df[(df["theme"] == t) & (df["year"] == y)]["growth_multiple"].iloc[0] for y in years]
                     for t in THEMES])
    norm = LogNorm(1, grid.max())
    fig, ax = C.new_figure()
    ax.imshow(grid, cmap=C.SEQUENTIAL, norm=norm, aspect="auto")
    hi, lo = _extremes(grid.ravel())
    for (i, j), v in np.ndenumerate(grid):
        pos = i * len(years) + j
        word = "\nHighest" if pos in hi else "\nLowest" if pos in lo else ""
        ax.text(j, i, f"{v:.1f}x{word}", ha="center", va="center", fontsize=C.LABEL, weight="bold",
                color="white" if norm(v) > 0.55 else C.INK)
        if word:
            ax.add_patch(plt.Rectangle((j - 0.46, i - 0.45), 0.92, 0.9, fill=False, edgecolor=C.HIGHLIGHT, lw=4))
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([f"{y - 1} to {y}" for y in years])
    ax.set_yticks(range(len(THEMES)))
    ax.set_yticklabels([C.THEME_LABELS[t] for t in THEMES])
    ax.xaxis.tick_top()
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_xticks(np.arange(-0.5, len(years)), minor=True)
    ax.set_yticks(np.arange(-0.5, len(THEMES)), minor=True)
    ax.grid(which="minor", color="white", lw=4)
    ax.tick_params(which="minor", length=0)
    fig.suptitle("Growth Multiple by Theme and Year")
    data.save(fig, "fig2b_growth_heatmap")


def fig2c_growth_by_theme(data: C.Data) -> None:
    """All four themes' growth multiples on one axis; each theme's peak year ringed and labelled."""
    df = data.fetch("fig2_growth_multiple")
    years = sorted(df["year"].unique())
    fig, ax = C.new_figure()
    for theme in THEMES:
        d = df[df["theme"] == theme].sort_values("year")
        C.line(ax, d["year"], d["growth_multiple"], C.COLORS[theme], label_text=C.THEME_LABELS[theme],
               marker=MARKERS[theme], partial_from=None)
        peak = d.loc[d["growth_multiple"].idxmax()]
        C.highlight_point(ax, peak["year"], peak["growth_multiple"],
                          f"{C.THEME_LABELS[theme]} peak {peak['growth_multiple']:.1f}x")
    ax.axhline(1, color=C.NEUTRAL, lw=1)
    ax.set_ylim(0, df["growth_multiple"].max() * 1.2)
    ax.set_yticks([0, 1, 5, 10, 15])
    ax.yaxis.set_major_formatter(times)
    ax.set_xticks(years)
    ax.set_xticklabels([f"{y - 1} to {y}" for y in years])
    ax.set_xlim(years[0] - 0.4, years[-1] + 0.4)
    ax.set_ylabel("Papers vs. previous year")
    C.legend(ax, loc="upper right")
    C.finish(fig, ax, "Year-over-Year Growth by Theme, 2022–2025")
    data.save(fig, "fig2c_growth_by_theme")


def fig3_phrase_yearly(data: C.Data) -> None:
    """Selected search phrases on one axis, labelled at the line ends (no legend).

    "ChatGPT" and "foundation model" are left out; "AI agent" and "LLM agent" are added into one
    line (a paper with both phrases counts twice there). The CSV keeps all eight phrases.
    """
    df = data.fetch("fig3_phrase_yearly")
    by_key = df.pivot(index="year", columns="query_key", values="works_cs").fillna(0)
    years = list(by_key.index)
    # (series, label, theme colour, marker): colour follows the theme, the marker the phrase.
    series = [
        (by_key["rag.retrieval_augmented_generation"], "RAG", "rag", "o"),
        (by_key["llm.large_language_model"], "LLM", "llm", "D"),
        (by_key["agents.ai_agent"] + by_key["agents.llm_agent"], "AI agent + LLM agent", "agents", "s"),
        (by_key["agents.agentic_ai"], "Agentic AI", "agents", "X"),
        (by_key["fm.pretrained_language_model"], "PLM", "foundation_models", "^"),
    ]
    fig, ax = C.new_figure()
    ends = []
    for values, name, theme, marker in series:
        values = values[values > 0]                       # a phrase not yet in use has no point
        C.line(ax, values.index, values.values, C.COLORS[theme], marker=marker,
               lw=4 if theme == "rag" else 3, zorder=5 if theme == "rag" else 3)
        ends.append((values.iloc[-1], f"{name} {values.iloc[-1]:,.0f}"))
    ax.set_yscale("log")
    ax.set_ylim(0.7, 200_000)
    ax.yaxis.set_major_formatter(thousands)
    ax.set_xticks(years)
    ax.set_xticklabels([f"{y}*" if y == PARTIAL else str(y) for y in years])
    ax.set_xlim(years[0] - 0.4, years[-1] + 0.3)
    ax.set_xlabel("Year (2026* to 22 Sep)")
    ax.set_ylabel("Papers per year (log scale)")
    C.finish(fig, ax, "Papers per Year by Search Phrase")
    C.end_labels(ax, PARTIAL, ends, gap=22)
    data.save(fig, "fig3_phrase_yearly")


def fig4_rag_per_100_llm(data: C.Data) -> None:
    df = data.fetch("fig4_rag_per_100_llm")
    scopes = {"cs": ("Computer science", C.BLUE, "o"), "all": ("All fields", C.NEUTRAL, "s")}
    fig, ax = C.new_figure()
    ends = []
    for scope, (name, color, marker) in scopes.items():
        d = df[df["field_scope"] == scope].sort_values("year")
        C.line(ax, d["year"], d["rag_per_100_llm"], color, label_text=name, marker=marker)
        ends.append((d["rag_per_100_llm"].iloc[-1], f"{name} {d['rag_per_100_llm'].iloc[-1]:.1f}"))
    cs = df[df["field_scope"] == "cs"].set_index("year")["rag_per_100_llm"]
    C.highlight_point(ax, cs.idxmin(), cs.min(), f"Lowest {cs.min():.1f}", dy=-20, va="top")
    C.highlight_point(ax, cs.idxmax(), cs.max(), "Highest", dy=16)
    years = sorted(df["year"].unique())
    ax.set_xticks(years)
    ax.set_xticklabels([f"{y}*" if y == PARTIAL else str(y) for y in years])
    ax.set_xlim(years[0] - 0.3, years[-1] + 0.3)
    ax.set_ylim(0, cs.max() * 1.25)
    ax.set_xlabel("Year (2026* to 22 Sep)")
    ax.set_ylabel("RAG papers per 100 LLM papers")
    C.legend(ax, loc="upper left")
    C.finish(fig, ax, "RAG Papers per 100 LLM Papers")
    C.end_labels(ax, years[-1], ends)
    data.save(fig, "fig4_rag_per_100_llm")


def main() -> None:
    data = C.Data(HERE)
    print(HERE.name)
    fig1_theme_yearly(data)
    fig2_growth_multiple(data)
    fig2b_growth_heatmap(data)
    fig2c_growth_by_theme(data)
    fig3_phrase_yearly(data)
    fig4_rag_per_100_llm(data)


if __name__ == "__main__":
    main()
