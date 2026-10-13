"""Direction 4: sub-directions inside RAG research, tagged by keyword rules.

    uv run python analysis/04_subfields/make_figures.py [--from-csv]

The rules live in subfield_rules.csv (one regular expression per sub-direction). They are sent to
the database as a parameter and matched there against each paper's title and abstract, so the
tagging itself is SQL. Edit the CSV and re-run to change, add or remove a sub-direction.
See README.md for each figure and for what a rule match does and does not mean.
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
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter
from scipy.cluster.hierarchy import leaves_list, linkage

HERE = Path(__file__).resolve().parent
if not (HERE / "common.py").exists():       # in the repo the shared module sits one level up;
    sys.path.insert(0, str(HERE.parent))    # a folder handed over on its own carries a copy
import common as C  # noqa: E402

BROAD, STRICT = "#86b6ef", "#256abf"            # one hue, two shades: any mention / in the title
KINDS = {"method": "Methods and concerns", "application": "Application domains"}
MATCH_NOTE = ("A paper is tagged when its title or abstract matches the sub-direction's keyword rule "
              "(subfield_rules.csv). Tags overlap; a mention is not the paper's main topic.")

thousands = FuncFormatter(lambda v, _: f"{v:,.0f}")
percent = FuncFormatter(lambda v, _: f"{v:.0f}%")


def fig1_subfield_sizes(data: C.Data, rules: dict) -> None:
    df = data.fetch("fig1_subfield_sizes", rules)
    cover = data.fetch("fig1b_tagging_coverage", rules).iloc[0]
    fig, axes = plt.subplots(2, 1, figsize=(8.8, 6.2), sharex=True,
                             gridspec_kw={"height_ratios": [(df["kind"] == k).sum() for k in KINDS]})
    for ax, (kind, heading) in zip(axes, KINDS.items()):
        d = df[df["kind"] == kind].sort_values("papers", ascending=False)
        y = np.arange(len(d))
        bars = ax.barh(y, d["papers"], 0.58, color=BROAD, label="Mentioned in title or abstract", zorder=3)
        ax.barh(y, d["papers_title"], 0.26, color=STRICT, label="Named in the title", zorder=4)
        C.bar_labels(ax, bars, [f"{r.papers:,}  ·  {r.share_pct:.0f}%" for r in d.itertuples()], horizontal=True)
        ax.set_yticks(y)
        ax.set_yticklabels(d["label"])
        ax.invert_yaxis()
        ax.set_title(heading, loc="left")
        ax.xaxis.set_major_formatter(thousands)
        C.style_axes(ax, grid="x")
    axes[0].set_xlim(0, df["papers"].max() * 1.18)
    axes[1].set_xlabel("RAG papers with an abstract, 2019–2026")
    C.legend(axes[0], loc="lower right", handlelength=1.0)
    fig.tight_layout(h_pad=1.4)
    C.titles(fig, "Sub-directions of RAG research by keyword rule",
             f"{cover['papers_tagged']:,.0f} of {cover['papers_with_abstract']:,.0f} papers with an abstract "
             f"({cover['tagged_pct']:.0f}%) match at least one rule; {cover['mean_tags_per_paper']:.1f} tags per paper on average.",
             f"{C.SOURCE} {MATCH_NOTE}\nPercentages are of papers with an abstract; "
             f"{cover['papers_without_abstract']:,.0f} papers without one are left out.")
    data.save(fig, "fig1_subfield_sizes")


def fig2_subfield_share_by_year(data: C.Data, rules: dict) -> None:
    df = data.fetch("fig2_subfield_share_by_year", rules)
    df = df[df["year"] >= 2024]                        # 2023 has too few papers for a stable share
    years = sorted(df["year"].unique())
    totals = df.groupby("year")["papers_with_abstract"].first()
    size = df[df["year"] == years[-1]].set_index("subfield")["share_pct"]
    ordered = [s for kind in KINDS
               for s in sorted(df.loc[df["kind"] == kind, "subfield"].unique(), key=lambda s: -size[s])]
    fig, axes = plt.subplots(3, 5, figsize=(13.2, 7.2), sharex=True, sharey=True)
    for ax in axes.flat[len(ordered):]:
        ax.axis("off")
    for ax, key in zip(axes.flat, ordered):
        d = df[df["subfield"] == key].sort_values("year")
        C.line(ax, d["year"], d["share_pct"], STRICT, label="Mentioned in title or abstract", partial_from=2026, ms=5)
        C.line(ax, d["year"], d["share_title_pct"], BROAD, label="Named in the title", partial_from=2026, ms=5)
        for column in ("share_pct", "share_title_pct"):
            ax.annotate(f"{d[column].iloc[-1]:.0f}%", (years[-1], d[column].iloc[-1]), xytext=(7, 0),
                        textcoords="offset points", ha="left", va="center", fontsize=8, color=C.INK_2)
        ax.set_title(d["label"].iloc[0], loc="left", fontsize=9.5)
        ax.text(0.0, 1.0, KINDS[d["kind"].iloc[0]], transform=ax.transAxes, fontsize=7.5, color=C.MUTED,
                va="top", ha="left")
        ax.set_xticks(years)
        ax.set_xlim(years[0] - 0.25, years[-1] + 0.75)
        ax.set_ylim(0, df["share_pct"].max() * 1.22)
        ax.yaxis.set_major_formatter(percent)
        ax.tick_params(labelbottom=True)
        C.style_axes(ax)
    fig.tight_layout(w_pad=1.0, h_pad=1.8)
    C.legend_below(fig, *axes.flat[0].get_legend_handles_labels(), ncol=2, handlelength=1.4, columnspacing=1.8)
    base = ", ".join(f"{totals[y]:,.0f} in {y}" for y in years)
    C.titles(fig, "How the sub-directions' shares of RAG papers moved, 2024–2026",
             "Share of each year's papers that match the rule. Hollow marker: 2026 is incomplete.",
             f"{C.SOURCE} {C.PARTIAL_2026} Papers with an abstract: {base}.\n{MATCH_NOTE}")
    data.save(fig, "fig2_subfield_share_by_year")


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
    # Order rows so that sub-directions with similar co-occurrence profiles sit next to each other.
    order = leaves_list(linkage(score, method="average", optimal_ordering=True))
    score, lift = score[np.ix_(order, order)], lift[np.ix_(order, order)]
    names = [labels[keys[i]] for i in order]
    shown = np.where(np.eye(len(names), dtype=bool), np.nan, score)

    norm = Normalize(-2, 2)
    fig, ax = plt.subplots(figsize=(8.6, 7.4))
    ax.imshow(shown, cmap=C.DIVERGING, norm=norm, aspect="equal")
    for i in range(len(names)):
        for j in range(len(names)):
            if i != j and not np.isnan(lift[i, j]) and abs(score[i, j]) >= np.log2(1.3):   # label clear departures only
                ax.text(j, i, f"{lift[i, j]:.1f}", ha="center", va="center", fontsize=7.5,
                        color="white" if abs(score[i, j]) > 1.1 else C.INK)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=40, ha="right", rotation_mode="anchor")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names)
    ax.set_xticks(np.arange(-0.5, len(names)), minor=True)
    ax.set_yticks(np.arange(-0.5, len(names)), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="both", length=0, colors=C.INK_2, labelsize=8.5)
    for spine in ax.spines.values():
        spine.set_visible(False)
    bar = fig.colorbar(ScalarMappable(norm=norm, cmap=C.DIVERGING), ax=ax, shrink=0.55, pad=0.03, ticks=[-2, -1, 0, 1, 2])
    bar.ax.set_yticklabels(["4x less", "2x less", "as chance", "2x more", "4x more"])
    bar.ax.tick_params(labelsize=8, colors=C.INK_2, length=0)
    bar.outline.set_visible(False)
    fig.tight_layout()
    C.titles(fig, "Which RAG sub-directions appear in the same paper",
             "How often two sub-directions co-occur compared with chance (lift). Numbers shown where the gap is at least 1.3x.",
             f"{C.SOURCE} Rows are ordered by hierarchical clustering of these profiles.\n{MATCH_NOTE}")
    data.save(fig, "fig3_subfield_cooccurrence")


def fig4_subfield_share_change(data: C.Data, rules: dict) -> None:
    df = data.fetch("fig4_subfield_share_change", rules).sort_values("change_pp", ascending=False)
    lengths = data.fetch("fig4b_abstract_length_by_year").set_index("year")
    length, title_length = lengths["mean_abstract_chars"], lengths["mean_title_chars"]
    growth = length[2026] / length[2024] - 1
    fig, ax = plt.subplots(figsize=(8.8, 5.4))
    y = np.arange(len(df))
    ax.barh(y, df["change_pp"], 0.56, color=np.where(df["change_pp"] >= 0, C.UP, C.DOWN), zorder=3)
    # The same change counted on titles only: a control that longer abstracts cannot move.
    ax.scatter(df["change_title_pp"], y, marker="D", s=30, color=C.INK, edgecolor="white", linewidth=1.0, zorder=5)
    reach = df["change_pp"].abs().max()
    for yi, row in zip(y, df.itertuples()):
        side = 1 if row.change_pp >= 0 else -1
        ax.annotate(f"{row.change_pp:+.1f} pp   ({row.share_2024_pct:.0f}% → {row.share_2026_pct:.0f}%)",
                    (row.change_pp, yi), xytext=(5 * side, 0), textcoords="offset points",
                    ha="left" if side > 0 else "right", va="center", fontsize=8, color=C.INK_2)
    ax.axvline(0, color=C.AXIS, lw=1, zorder=2)
    ax.set_yticks(y)
    ax.set_yticklabels(df["label"])
    ax.invert_yaxis()
    ax.set_xlim(-reach * 1.75, reach * 1.75)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.0f}" if v else "0"))
    ax.set_xlabel("Change in share of RAG papers, 2024 to 2026 (percentage points)")
    C.style_axes(ax, grid="x")
    ax.spines["left"].set_visible(False)
    handles = [Patch(facecolor=C.NEUTRAL), Line2D([], [], marker="D", ls="", color=C.INK, markeredgecolor="white", ms=6.5)]
    C.legend(ax, handles=handles, labels=["Bar: mentioned in title or abstract", "Diamond: named in the title"],
             loc="lower right", handlelength=1.0)
    fig.tight_layout()
    C.titles(fig, "Sub-directions gaining and losing share of RAG papers, 2024 to 2026",
             "Share of the year's papers matching each rule: 2026 minus 2024. Blue gained, red lost.",
             f"{C.SOURCE} {C.PARTIAL_2026} {MATCH_NOTE}\n"
             f"Abstracts grew {growth:.0%} longer from 2024 to 2026 ({length[2024]:,.0f} to {length[2026]:,.0f} characters), "
             f"which lifts every bar a little. Titles grew less ({title_length[2024]:,.0f} to {title_length[2026]:,.0f}), "
             "so the diamonds are the steadier measure.")
    data.save(fig, "fig4_subfield_share_change")


def main() -> None:
    data = C.Data(HERE)
    print(HERE.name)
    rules_table = pd.read_csv(HERE / "subfield_rules.csv", keep_default_na=False)
    rules = {"rules": json.dumps(rules_table.to_dict("records"))}
    fig1_subfield_sizes(data, rules)
    fig2_subfield_share_by_year(data, rules)
    fig3_subfield_cooccurrence(data, rules)
    fig4_subfield_share_change(data, rules)


if __name__ == "__main__":
    main()
