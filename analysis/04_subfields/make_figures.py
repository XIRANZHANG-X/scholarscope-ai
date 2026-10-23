"""Direction 4: sub-directions inside RAG research, tagged by keyword rules.

    uv run python analysis/04_subfields/make_figures.py [--from-csv]

The rules live in subfield_rules.csv (one regular expression per sub-direction). They are sent to
the database as a parameter and matched there against each paper's title and abstract, so the
tagging itself is SQL. Edit the CSV and re-run to change, add or remove a sub-direction.

One encoding runs through every figure: light blue = mentioned in
title or abstract (broad), dark blue = named in the title (strict). House style from common.py;
one plot per figure. See README.md for each figure and what a rule match does and does not mean.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter
from scipy.cluster.hierarchy import leaves_list, linkage

HERE = Path(__file__).resolve().parent
if not (HERE / "common.py").exists():       # in the repo the shared module sits one level up;
    sys.path.insert(0, str(HERE.parent))    # a folder handed over on its own carries a copy
import common as C  # noqa: E402

BROAD, STRICT = C.LIGHT_BLUE, C.DARK_BLUE
BROAD_LABEL, STRICT_LABEL = "Mentioned in title or abstract", "Named in the title"
KINDS = {"method": "Methods and Concerns", "application": "Application Domains"}

thousands = FuncFormatter(lambda v, _: f"{v:,.0f}")
percent = FuncFormatter(lambda v, _: f"{v:.0f}%")


def fig1_subfield_sizes(data: C.Data, rules: dict) -> None:
    """Two figures, one per group of sub-directions, on the same scale."""
    df = data.fetch("fig1_subfield_sizes", rules)
    data.fetch("fig1b_tagging_coverage", rules)            # totals for the README
    for kind, heading in KINDS.items():
        d = df[df["kind"] == kind].sort_values("papers", ascending=False)
        fig, ax = C.new_figure()
        y = np.arange(len(d))
        bars = ax.barh(y, d["papers"], 0.66, color=BROAD, label=BROAD_LABEL, zorder=3)
        ax.barh(y, d["papers_title"], 0.32, color=STRICT, label=STRICT_LABEL, zorder=4)
        C.bar_labels(ax, bars, [f"{r.papers:,} ({r.share_pct:.0f}%)" for r in d.itertuples()], horizontal=True)
        for yi, row in zip(y, d.itertuples()):
            ax.text(row.papers_title + 25, yi, f"{row.papers_title:,}", ha="left", va="center",
                    fontsize=C.LABEL, weight="bold", color=C.INK, zorder=5)
        ax.set_yticks(y)
        ax.set_yticklabels(d["label"])
        ax.invert_yaxis()
        ax.set_xlim(0, df["papers"].max() * 1.22)
        ax.xaxis.set_major_formatter(thousands)
        ax.set_xlabel("RAG papers with an abstract, 2019–2026")
        C.legend(ax, loc="lower right")
        C.finish(fig, ax, f"RAG Sub-directions: {heading}")
        data.save(fig, f"fig1_sizes_{kind}")


def fig2_subfield_share_shift(data: C.Data, rules: dict) -> None:
    """Dumbbell: each sub-direction's share in 2024 (hollow) and 2026* (filled), blue if it grew,
    red if it shrank; rows sorted by the change. Replaces the 13 per-sub-direction line charts."""
    df = data.fetch("fig2_subfield_share_by_year", rules)
    broad = df.pivot(index="label", columns="year", values="share_pct")
    first, last = 2024, C.PARTIAL_YEAR                  # 2023 has too few papers for a stable share
    change = (broad[last] - broad[first]).sort_values()
    fig, ax = C.new_figure()
    y = np.arange(len(change))
    for yi, (name, delta) in zip(y, change.items()):
        a, b = broad.loc[name, first], broad.loc[name, last]
        colour = C.UP if delta > 0.5 else C.DOWN if delta < -0.5 else C.NEUTRAL
        ax.plot([a, b], [yi, yi], color=colour, lw=5, solid_capstyle="butt", alpha=0.45, zorder=2)
        ax.scatter(a, yi, s=170, facecolor="white", edgecolor=colour, linewidth=3, zorder=3)
        ax.scatter(b, yi, s=170, color=colour, edgecolor="white", linewidth=1.5, zorder=4)
        C.label(ax, max(a, b), yi, f"{b:.0f}%  ({delta:+.0f} pp)", dx=14, dy=0, ha="left", va="center",
                color=C.INK_2 if colour == C.NEUTRAL else colour)
    ax.set_yticks(y)
    ax.set_yticklabels(change.index)
    ax.set_xlim(0, broad[[first, last]].to_numpy().max() * 1.28)
    ax.xaxis.set_major_formatter(percent)
    ax.set_xlabel(f"Share of the year's RAG papers ({BROAD_LABEL.lower()})")
    handles = [Line2D([], [], marker="o", ls="", ms=12, markerfacecolor="white", markeredgecolor=C.INK_2, mew=3),
               Line2D([], [], marker="o", ls="", ms=12, color=C.INK_2)]
    C.legend(ax, handles=handles, labels=[str(first), f"{last}* (to 22 Sep)"], loc="lower right")
    C.finish(fig, ax, f"How Sub-direction Shares Shifted, {first} to {last}")
    data.save(fig, "fig2_subfield_share_shift")


def fig3_subfield_cooccurrence(data: C.Data, rules: dict) -> None:
    df = data.fetch("fig3_subfield_cooccurrence", rules)
    labels = df.drop_duplicates("subfield_a").set_index("subfield_a")["label_a"]
    keys = list(labels.index)
    lift = (df.pivot(index="subfield_a", columns="subfield_b", values="lift")
            .reindex(index=keys, columns=keys).to_numpy(dtype=float))
    # log2 makes "twice as often" and "half as often" equally far from chance (lift 1 -> 0).
    with np.errstate(divide="ignore"):
        score = np.clip(np.log2(np.where(np.isnan(lift), 1.0, np.maximum(lift, 0.25))), -2, 2)
    np.fill_diagonal(score, 0.0)
    # Rows ordered so that sub-directions with similar co-occurrence profiles sit together.
    order = leaves_list(linkage(score, method="average", optimal_ordering=True))
    score, lift = score[np.ix_(order, order)], lift[np.ix_(order, order)]
    names = [labels[keys[i]] for i in order]
    n = len(names)
    tri = np.where(np.tril(np.ones((n, n), dtype=bool), -1), score, np.nan)[1:, :-1]   # each pair once
    lower = np.where(np.tril(np.ones((n, n), dtype=bool), -1), lift, np.nan)
    hi = np.unravel_index(np.nanargmax(lower), lower.shape)
    lo = np.unravel_index(np.nanargmin(lower), lower.shape)

    norm = Normalize(-2, 2)
    fig, ax = C.new_figure()
    ax.imshow(tri, cmap=C.DIVERGING, norm=norm, aspect="auto")
    for i in range(1, n):
        for j in range(i):
            if not np.isnan(lift[i, j]) and abs(score[i, j]) >= np.log2(1.3):   # clear departures only
                ax.text(j, i - 1, f"{lift[i, j]:.1f}", ha="center", va="center", fontsize=C.LABEL - 2,
                        weight="bold", color="white" if abs(score[i, j]) > 1.1 else C.INK)
    for (i, j), word in ((hi, "Highest"), (lo, "Lowest")):
        ax.add_patch(plt.Rectangle((j - 0.48, i - 1 - 0.48), 0.96, 0.96, fill=False, edgecolor=C.HIGHLIGHT, lw=3.5))
    ax.set_xticks(range(n - 1))
    ax.set_xticklabels(names[:-1], rotation=35, ha="right", rotation_mode="anchor")
    ax.set_yticks(range(n - 1))
    ax.set_yticklabels(names[1:])
    ax.set_xticks(np.arange(-0.5, n - 1), minor=True)
    ax.set_yticks(np.arange(-0.5, n - 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="minor", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.text(0.98, 0.98, f"Highest: {names[hi[0]]} × {names[hi[1]]} ({lift[hi]:.1f}x)\n"
                        f"Lowest: {names[lo[0]]} × {names[lo[1]]} ({lift[lo]:.1f}x)",
            transform=ax.transAxes, ha="right", va="top", fontsize=C.LABEL, weight="bold", color=C.HIGHLIGHT)
    bar = fig.colorbar(ScalarMappable(norm=norm, cmap=C.DIVERGING), ax=ax, shrink=0.7, pad=0.02,
                       ticks=[-2, -1, 0, 1, 2])
    bar.ax.set_yticklabels(["¼x", "½x", "1x", "2x", "4x"])
    bar.set_label("Co-occurrence vs. chance (lift)", fontsize=C.AXIS_TITLE, weight="bold")
    bar.ax.tick_params(length=0, labelsize=C.TICK)
    bar.outline.set_visible(False)
    fig.suptitle("Co-occurrence of RAG Sub-directions")
    data.save(fig, "fig3_subfield_cooccurrence")


def tables_for_readme(data: C.Data, rules: dict) -> None:
    """No figure: the 2024 -> 2026* change per sub-direction, broad and title-only (the dumbbell
    shows the broad change; the title-only column is quoted in the README), and the abstract
    length check."""
    data.fetch("fig4_subfield_share_change", rules)
    data.fetch("fig4b_abstract_length_by_year")


def main() -> None:
    data = C.Data(HERE)
    print(HERE.name)
    rules_table = pd.read_csv(HERE / "subfield_rules.csv", keep_default_na=False)
    rules = {"rules": json.dumps(rules_table.to_dict("records"))}
    fig1_subfield_sizes(data, rules)
    fig2_subfield_share_shift(data, rules)
    fig3_subfield_cooccurrence(data, rules)
    tables_for_readme(data, rules)


if __name__ == "__main__":
    main()
