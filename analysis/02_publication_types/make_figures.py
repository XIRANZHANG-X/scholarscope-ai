"""Direction 2: preprints, conference papers and journal articles in the RAG corpus.

    uv run python analysis/02_publication_types/make_figures.py [--from-csv]

Four figures from core.works / core.sources (plus meta.recall_probes for the theme comparison).
The type is OpenAlex's `type` field. See README.md for each figure and its caveats.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter

HERE = Path(__file__).resolve().parent
if not (HERE / "common.py").exists():       # in the repo the shared module sits one level up;
    sys.path.insert(0, str(HERE.parent))    # a folder handed over on its own carries a copy
import common as C  # noqa: E402

# Colour follows the publication type in every figure of this direction; stacks use this order.
TYPES = {
    "preprint": ("Preprint", C.SERIES[0]),
    "conference-paper": ("Conference paper", C.SERIES[1]),
    "article": ("Article", C.SERIES[2]),
    "review": ("Review", C.SERIES[3]),
}
MAIN_TYPES = ["preprint", "conference-paper", "article"]
THEME_LABELS = {"rag": "RAG", "llm": "LLM", "agents": "Agents", "foundation_models": "Foundation models"}

thousands = FuncFormatter(lambda v, _: f"{v:,.0f}")
percent = FuncFormatter(lambda v, _: f"{v:.0f}%")


def fig1_type_by_quarter(data: C.Data) -> None:
    df = data.fetch("fig1_type_by_quarter")
    wide = df.pivot(index="quarter", columns="type", values="works").fillna(0).sort_index()
    fig, ax = plt.subplots(figsize=(9.2, 4.8))
    x = np.arange(len(wide))
    bottom = np.zeros(len(wide))
    for key, (label, color) in TYPES.items():
        # The white edge is the 2px surface gap that separates stacked segments.
        ax.bar(x, wide[key], 0.52, bottom=bottom, color=color, edgecolor="white", linewidth=1.2,
               label=label, zorder=3)
        bottom += wide[key].to_numpy()
    for xi, total in zip(x, bottom):
        ax.annotate(f"{total:,.0f}", (xi, total), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=8, color=C.INK_2)
    # Conference papers reach OpenAlex months late, so the newest quarters are not a real decline.
    lagging = {"2026Q2", "2026Q3"}
    ax.set_xticks(x)
    ax.set_xticklabels([q.replace("Q", " Q") + ("*" if q in lagging else "") for q in wide.index])
    ax.set_ylim(0, bottom.max() * 1.12)
    ax.yaxis.set_major_formatter(thousands)
    ax.set_ylabel("Papers per quarter")
    C.style_axes(ax)
    C.legend(ax, loc="upper left", ncol=4, handlelength=1.0, columnspacing=1.4)
    ax.text(0.012, 0.90, "* conference papers from these quarters are still being indexed, not a real decline",
            transform=ax.transAxes, fontsize=8, color=C.MUTED, ha="left", va="top",
            bbox=dict(facecolor="white", edgecolor="none", pad=1.5))
    fig.tight_layout()
    year_only = df.groupby(df["quarter"].str[:4])["year_only_date"].first()
    left_out = ", ".join(f"{n:,} in {y}" for y, n in year_only.items())
    C.titles(fig, "RAG papers per quarter by publication type",
             "Papers with a known publication month, 2024 Q1 to 2026 Q3 (the last quarter ends 22 Sep).",
             f"{C.SOURCE} Type is OpenAlex's `type` field.\n"
             f"Left out because OpenAlex knows only their year: {left_out}.")
    data.save(fig, "fig1_type_by_quarter")


def _stacked_share(ax, rows: list[str], shares: dict[str, list[float]]) -> None:
    """100% stacked horizontal bars; a segment is labelled only when its share has room."""
    y = np.arange(len(rows))
    left = np.zeros(len(rows))
    for key, (label, color) in TYPES.items():
        values = np.array(shares[key])
        ax.barh(y, values, 0.5, left=left, color=color, edgecolor="white", linewidth=1.2, label=label, zorder=3)
        for yi, start, value in zip(y, left, values):
            if value >= 7:
                ax.text(start + value / 2, yi, f"{value:.0f}%", ha="center", va="center", fontsize=8,
                        color="white" if key in ("preprint", "conference-paper") else C.INK)
        left += values
    ax.set_yticks(y)
    ax.set_yticklabels(rows)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(percent)
    C.style_axes(ax, grid="x")


def fig2_type_share(data: C.Data) -> None:
    by_year = data.fetch("fig2a_type_share_by_year")
    by_theme = data.fetch("fig2b_type_mix_by_theme")
    fig, (ax_year, ax_theme) = plt.subplots(1, 2, figsize=(11.2, 3.5))

    years = sorted(by_year["year"].unique())
    year_totals = by_year.groupby("year")["works_in_year"].first()
    year_pivot = by_year.pivot(index="year", columns="type", values="share_pct").fillna(0)
    _stacked_share(ax_year, [f"{y}  (n = {year_totals[y]:,.0f})" for y in years],
                   {k: year_pivot.loc[years, k].tolist() for k in TYPES})
    ax_year.set_title("RAG corpus, by publication year", loc="left")

    themes = list(THEME_LABELS)
    theme_totals = by_theme.groupby("theme")["works_in_theme"].first()
    theme_pivot = by_theme.pivot(index="theme", columns="type", values="share_pct").fillna(0)
    _stacked_share(ax_theme, [f"{THEME_LABELS[t]}  (n = {theme_totals[t]:,.0f})" for t in themes],
                   {k: theme_pivot.loc[themes, k].tolist() for k in TYPES})
    ax_theme.set_title("RAG next to the other themes, 2019–2026", loc="left")

    fig.tight_layout(w_pad=3.0)
    C.legend_below(fig, *ax_year.get_legend_handles_labels(), ncol=4, handlelength=1.0, columnspacing=1.6)
    C.titles(fig, "Publication-type mix of RAG papers",
             "Share of papers by OpenAlex type. Left: the RAG corpus year by year. Right: computer-science papers of each theme.",
             f"{C.SOURCE} {C.PARTIAL_2026} Reviews are under 1% everywhere and too thin to label.\n"
             "Theme counts add up several search phrases for LLM, Agents and Foundation models (n is an upper bound).")
    data.save(fig, "fig2_type_share")


def fig3_citations_by_type(data: C.Data) -> None:
    df = data.fetch("fig3_citations_by_type")
    df = df[df["type"].isin(MAIN_TYPES)]
    cohorts = {2024: ("Published 2024", "#256abf"), 2025: ("Published 2025", "#86b6ef")}
    panels = [("cited_share_pct", "Papers cited at least once", lambda v: f"{v:.0f}%", percent),
              ("mean_citations", "Mean citations per paper", lambda v: f"{v:.1f}", None)]
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 3.3), sharey=True)
    y = np.arange(len(MAIN_TYPES))
    for ax, (column, title, fmt, formatter) in zip(axes, panels):
        table = df.pivot(index="type", columns="cohort_year", values=column).loc[MAIN_TYPES]
        counts = df.pivot(index="type", columns="cohort_year", values="works").loc[MAIN_TYPES]
        ax.hlines(y, table[2025], table[2024], color=C.AXIS, lw=1.6, zorder=2)   # dumbbell bar
        for year, (label, color) in cohorts.items():
            ax.scatter(table[year], y, s=70, color=color, edgecolor="white", linewidth=1.4, zorder=3, label=label)
            for yi, value in zip(y, table[year]):
                ax.annotate(fmt(value), (value, yi), xytext=(0, 9), textcoords="offset points",
                            ha="center", va="bottom", fontsize=8, color=C.INK_2)
        ax.set_yticks(y)
        ax.set_yticklabels([f"{TYPES[t][0]}\n{counts.loc[t, 2024]:,.0f} / {counts.loc[t, 2025]:,.0f} papers"
                            for t in MAIN_TYPES])
        ax.set_ylim(len(MAIN_TYPES) - 0.45, -0.75)
        ax.set_xlim(0, table.to_numpy().max() * 1.12)
        if formatter:
            ax.xaxis.set_major_formatter(formatter)
        ax.set_title(title, loc="left")
        C.style_axes(ax, grid="x")
    fig.tight_layout(w_pad=2.5)
    C.legend_below(fig, *axes[0].get_legend_handles_labels(), ncol=2, handletextpad=0.3, columnspacing=1.6)
    C.titles(fig, "Citations by publication type, same publication year",
             "Each type compared within one cohort, because older papers have had longer to be cited. Paper counts: 2024 / 2025.",
             f"{C.SOURCE} Citations counted up to that date. Reviews are left out of the chart (5 and 22 papers).")
    data.save(fig, "fig3_citations_by_type")


def _short(name: str, limit: int = 46) -> str:
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).rstrip(". ")   # "arXiv (Cornell University)" -> "arXiv"
    if name.isupper() and " " in name:                           # a shouted title, not an acronym
        name = name.title()
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def fig4_top_sources_by_type(data: C.Data) -> None:
    df = data.fetch("fig4_top_sources_by_type")
    fig, axes = plt.subplots(1, 3, figsize=(13.6, 4.4))
    for ax, key in zip(axes, MAIN_TYPES):
        label, color = TYPES[key]
        d = df[df["type"] == key].sort_values("rank_in_type")
        bars = ax.barh(np.arange(len(d)), d["works"], 0.55, color=color, zorder=3)
        C.bar_labels(ax, bars, [f"{v:,.0f}" for v in d["works"]], horizontal=True)
        ax.set_yticks(np.arange(len(d)))
        ax.set_yticklabels([_short(n) for n in d["source_name"]])
        ax.invert_yaxis()
        ax.set_xlim(0, d["works"].max() * 1.16)
        ax.xaxis.set_major_formatter(thousands)
        total, missing = d["works_in_type"].iloc[0], d["works_without_source"].iloc[0]
        ax.set_title(f"{label}: {total:,.0f} papers", loc="left", pad=19)
        ax.annotate(f"{missing:,.0f} ({missing / total:.0%}) have no venue recorded", (0, 1),
                    xycoords="axes fraction", xytext=(0, 5), textcoords="offset points",
                    fontsize=8, color=C.MUTED, ha="left", va="bottom")
        C.style_axes(ax, grid="x")
    fig.tight_layout(w_pad=2.0)
    C.titles(fig, "Where RAG papers appear: the ten most common venues per type",
             "Papers per venue, 2019–2026. Each panel has its own scale.",
             f"{C.SOURCE} Venue names as recorded by OpenAlex, shortened. "
             "Zenodo is a repository, yet OpenAlex types 768 of its deposits as articles.")
    data.save(fig, "fig4_top_sources_by_type")


def main() -> None:
    data = C.Data(HERE)
    print(HERE.name)
    fig1_type_by_quarter(data)
    fig2_type_share(data)
    fig3_citations_by_type(data)
    fig4_top_sources_by_type(data)


if __name__ == "__main__":
    main()
