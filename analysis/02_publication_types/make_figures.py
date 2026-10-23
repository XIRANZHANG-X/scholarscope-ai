"""Direction 2: preprints, conference papers and articles in the RAG corpus.

    uv run python analysis/02_publication_types/make_figures.py [--from-csv]

Data: core.works / core.sources (plus meta.recall_probes for the theme comparison). The type is
OpenAlex's `type` field. Quarters are grouped by year with the incomplete period shaded,
citations are compared within a publication-year cohort, and venues are ranked per type. House style from common.py; one plot per
figure. See README.md for each figure and its caveats.
"""

from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import numpy as np
from matplotlib.colors import to_rgb
from matplotlib.ticker import FuncFormatter

HERE = Path(__file__).resolve().parent
if not (HERE / "common.py").exists():       # in the repo the shared module sits one level up;
    sys.path.insert(0, str(HERE.parent))    # a folder handed over on its own carries a copy
import common as C  # noqa: E402

TYPES = list(C.TYPE_LABELS)                  # stacks use this order
MAIN_TYPES = ["preprint", "conference-paper", "article"]
PLURAL = {"preprint": "Preprints", "conference-paper": "Conference Papers", "article": "Articles"}

thousands = FuncFormatter(lambda v, _: f"{v:,.0f}")
percent = FuncFormatter(lambda v, _: f"{v:.0f}%")


def fig1_type_by_quarter(data: C.Data) -> None:
    df = data.fetch("fig1_type_by_quarter")
    wide = df.pivot(index="quarter", columns="type", values="works").fillna(0).sort_index()
    fig, ax = C.new_figure()
    x = np.arange(len(wide))
    bottom = np.zeros(len(wide))
    for key in TYPES:
        ax.bar(x, wide[key], 0.62, bottom=bottom, color=C.COLORS[key], edgecolor="white", linewidth=1.5,
               label=C.TYPE_LABELS[key], zorder=3)
        bottom += wide[key].to_numpy()
    for xi, total in zip(x, bottom):
        C.label(ax, xi, total, f"{total:,.0f}")
    # The last two quarters: conference papers are still being indexed, Q3 ends on 22 Sep.
    ax.axvspan(len(wide) - 2.5, len(wide) - 0.5, color=C.LAND, zorder=0)
    C.label(ax, len(wide) - 1.5, bottom.max() * 1.13, "Incomplete*", dy=0)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{q[-2:]}\n{q[:4]}" if q.endswith("Q1") or i == 0 else q[-2:]
                        for i, q in enumerate(wide.index)])
    ax.set_xlim(-0.6, len(wide) - 0.4)
    ax.set_ylim(0, bottom.max() * 1.25)
    ax.yaxis.set_major_formatter(thousands)
    ax.set_xlabel("Quarter (papers dated to the year only are excluded)")
    ax.set_ylabel("Papers")
    C.legend(ax, loc="upper left", ncol=4)
    C.finish(fig, ax, "RAG Papers per Quarter by Publication Type")
    data.save(fig, "fig1_type_by_quarter")


def _stacked_share(ax, rows: list[str], shares: dict[str, list[float]]) -> None:
    """100% stacked horizontal bars; a segment is labelled only when its share has room."""
    y = np.arange(len(rows))
    left = np.zeros(len(rows))
    for key in TYPES:
        values = np.array(shares[key])
        ax.barh(y, values, 0.55, left=left, color=C.COLORS[key], edgecolor="white", linewidth=1.5,
                label=C.TYPE_LABELS[key], zorder=3)
        for yi, start, value in zip(y, left, values):
            if value >= 7:
                ax.text(start + value / 2, yi, f"{value:.0f}%", ha="center", va="center", fontsize=C.LABEL,
                        weight="bold", color="white" if key != "review" else C.INK)
        left += values
    ax.set_yticks(y)
    ax.set_yticklabels(rows)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(percent)
    ax.set_xlabel("Share of papers")


def fig2_type_share(data: C.Data) -> None:
    by_year = data.fetch("fig2a_type_share_by_year")
    years = [2024, 2025, 2026]
    table = by_year.pivot(index="year", columns="type", values="share_pct").fillna(0)
    fig, ax = C.new_figure()
    _stacked_share(ax, ["2024", "2025", "2026*"], {k: table.loc[years, k].tolist() for k in TYPES})
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=4, frameon=False)
    C.finish(fig, ax, "Publication Type Mix by Year")
    data.save(fig, "fig2_type_share_by_year")

    by_theme = data.fetch("fig2b_type_mix_by_theme")
    themes = list(C.THEME_LABELS)
    table = by_theme.pivot(index="theme", columns="type", values="share_pct").fillna(0)
    fig, ax = C.new_figure()
    _stacked_share(ax, [C.THEME_LABELS[t] for t in themes], {k: table.loc[themes, k].tolist() for k in TYPES})
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=4, frameon=False)
    C.finish(fig, ax, "Publication Type Mix by Research Theme")
    data.save(fig, "fig2b_type_mix_by_theme")


def _tint(color: str, amount: float = 0.5) -> tuple[float, float, float]:
    """The colour mixed with white: the same type, a lighter shade."""
    r, g, b = to_rgb(color)
    return (r + (1 - r) * amount, g + (1 - g) * amount, b + (1 - b) * amount)


def fig3_citations_by_type(data: C.Data) -> None:
    """Two figures (share cited, mean citations): per type, the 2024 cohort (full colour) beside the
    2025 cohort (light shade). Compare types within a cohort; the 2025 papers have had a year less."""
    df = data.fetch("fig3_citations_by_type")
    df = df[df["type"].isin(MAIN_TYPES)]
    cohorts = {2024: 0.0, 2025: 0.55}                      # cohort -> how much white is mixed in
    measures = {"cited_share_pct": ("Share of Papers Cited at Least Once", "Share of papers cited",
                                    lambda v: f"{v:.0f}%", percent, 100),
                "mean_citations": ("Mean Citations per Paper", "Mean citations per paper",
                                   lambda v: f"{v:.1f}", None, df["mean_citations"].max())}
    for column, (title, ylabel, fmt, formatter, ymax) in measures.items():
        fig, ax = C.new_figure()
        x = np.arange(len(MAIN_TYPES))
        width = 0.36
        for j, (year, amount) in enumerate(cohorts.items()):
            d = df[df["cohort_year"] == year].set_index("type").loc[MAIN_TYPES]
            bars = ax.bar(x + (j - 0.5) * width, d[column], width * 0.92, zorder=3,
                          color=[_tint(C.COLORS[t], amount) for t in MAIN_TYPES])
            C.bar_labels(ax, bars, [fmt(v) for v in d[column]])
            # Legend proxies in neutral grey: the shade, not the hue, tells the cohorts apart.
            ax.bar(0, 0, color=_tint(C.INK_2, amount + 0.1), label=f"Published in {year}")
        n = df.pivot(index="type", columns="cohort_year", values="works")
        ax.set_xticks(x)
        ax.set_xticklabels([f"{C.TYPE_LABELS[t]}\n(n = {n.loc[t, 2024]:,} / {n.loc[t, 2025]:,})"
                            for t in MAIN_TYPES])
        ax.set_xlim(-0.6, len(MAIN_TYPES) - 0.4)
        ax.set_ylim(0, ymax * 1.15)
        if formatter:
            ax.yaxis.set_major_formatter(formatter)
        ax.set_xlabel("Publication type (papers published in 2024 / 2025)")
        ax.set_ylabel(f"{ylabel} (to 22 Sep 2026)")
        C.legend(ax, loc="upper left")
        C.finish(fig, ax, title)
        data.save(fig, f"fig3_{column.replace('_pct', '')}")


def _short(name: str) -> str:
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).rstrip(". ")   # "arXiv (Cornell University)" -> "arXiv"
    if name.isupper() and " " in name:                           # a shouted title, not an acronym
        name = name.title()
    return textwrap.fill(name, 34)


def fig4_top_sources_by_type(data: C.Data) -> None:
    """Per type: its five most common venues, and how many papers have no venue at all."""
    df = data.fetch("fig4_top_sources_by_type")
    for key in MAIN_TYPES:
        d = df[df["type"] == key].sort_values("rank_in_type").head(5)
        missing = int(d["works_without_source"].iloc[0])
        names = [_short(n) for n in d["source_name"]] + ["No venue recorded"]
        values = list(d["works"]) + [missing]
        total = int(d["works_in_type"].iloc[0])
        fig, ax = C.new_figure()
        y = np.arange(len(values))
        bars = ax.barh(y, values, 0.6, color=[C.COLORS[key]] * len(d) + [C.NEUTRAL], zorder=3)
        C.bar_labels(ax, bars, [f"{v:,}  ({v / total:.0%})" for v in values], horizontal=True)
        ax.set_yticks(y)
        ax.set_yticklabels(names)
        ax.invert_yaxis()
        ax.set_xlim(0, max(values) * 1.3)
        ax.xaxis.set_major_formatter(thousands)
        ax.set_xlabel(f"Papers (of {total:,} {PLURAL[key].lower()})")
        C.finish(fig, ax, f"Top Venues: {PLURAL[key]}")
        data.save(fig, f"fig4_sources_{key.replace('-', '_')}")


def main() -> None:
    data = C.Data(HERE)
    print(HERE.name)
    fig1_type_by_quarter(data)
    fig2_type_share(data)
    fig3_citations_by_type(data)
    fig4_top_sources_by_type(data)


if __name__ == "__main__":
    main()
