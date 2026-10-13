"""Direction 3: where RAG research is done and who works with whom.

    uv run python analysis/03_world_collaboration/make_figures.py [--from-csv]

Six figures from bridge.work_countries and the World Bank country profiles. Countries are
counted in full: a paper with authors in three countries counts once for each.
See README.md for each figure and its caveats.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LogNorm, PowerNorm
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter

HERE = Path(__file__).resolve().parent
if not (HERE / "common.py").exists():       # in the repo the shared module sits one level up;
    sys.path.insert(0, str(HERE.parent))    # a folder handed over on its own carries a copy
import common as C  # noqa: E402
import worldmap  # noqa: E402

SHORT_NAMES = {"Hong Kong SAR, China": "Hong Kong", "Macao SAR, China": "Macao", "Korea, Rep.": "South Korea",
               "Russian Federation": "Russia", "Viet Nam": "Vietnam", "Turkiye": "Türkiye",
               "Iran, Islamic Rep.": "Iran", "Egypt, Arab Rep.": "Egypt"}
COVERAGE_NOTE = ("Only papers with a known country are counted (9,335 of 15,921). "
                 "A paper with authors in several countries counts once for each.")

thousands = FuncFormatter(lambda v, _: f"{v:,.0f}")
percent = FuncFormatter(lambda v, _: f"{v:.0f}%")
halo = [pe.withStroke(linewidth=2.2, foreground="white")]


def short(name: str) -> str:
    return SHORT_NAMES.get(name, name)


def fig1_world_map(data: C.Data) -> None:
    df = data.fetch("fig1_papers_by_country")
    norm = LogNorm(vmin=1, vmax=df["papers"].max())
    colour = {row.country_code: C.SEQUENTIAL(norm(row.papers)) for row in df.itertuples()}
    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    drawn = worldmap.draw_base(ax, colour, C.LAND)

    # Territories too small for the 1:110m outlines get a dot in the same colour scale.
    dots = df[~df["country_code"].isin(drawn) & df["latitude"].notna()]
    x, y = worldmap.project(dots["longitude"], dots["latitude"])
    ax.scatter(x, y, s=30, c=[colour[c] for c in dots["country_code"]], edgecolor="white", linewidth=1.0, zorder=3)

    inside = {"CN": (103, 35), "US": (-99, 39), "IN": (79, 22)}      # (lon, lat) well inside the country
    for row in df[df["country_code"].isin(inside)].itertuples():
        px, py = worldmap.project(*inside[row.country_code])
        ax.text(px, py, f"{short(row.country)}\n{row.papers:,}", ha="center", va="center", fontsize=8,
                color="white", path_effects=[pe.withStroke(linewidth=2.0, foreground="#0d366b")], zorder=4)

    bar = fig.colorbar(ScalarMappable(norm=norm, cmap=C.SEQUENTIAL), cax=ax.inset_axes([0.02, 0.10, 0.20, 0.025]),
                       orientation="horizontal", ticks=[1, 10, 100, 1000])
    bar.ax.xaxis.set_major_formatter(thousands)
    bar.ax.tick_params(which="both", labelsize=7.5, colors=C.INK_2, length=0)
    bar.ax.minorticks_off()
    bar.outline.set_visible(False)
    bar.ax.set_title("RAG papers (log scale)", fontsize=8, color=C.INK_2, loc="left", pad=4)
    ax.text(0.02, 0.035, "Grey: no paper with a known country", transform=ax.transAxes, fontsize=7.5, color=C.MUTED)

    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    dotted = ", ".join(f"{short(r.country)} ({r.papers:,})" for r in dots.sort_values("papers", ascending=False).head(4).itertuples())
    C.titles(fig, "Where RAG papers come from",
             f"Papers per country, 2019–2026, in {len(df)} countries. Colour is on a log scale.",
             f"{C.SOURCE} {COVERAGE_NOTE}\nDots mark places too small for the map outline: {dotted} and others. "
             "Outlines: Natural Earth 1:110m.")
    data.save(fig, "fig1_world_map")


def fig2_top_countries_collab(data: C.Data) -> None:
    df = data.fetch("fig2_top_countries_collab")
    fig, ax = plt.subplots(figsize=(8.6, 5.4))
    y = np.arange(len(df))
    ax.barh(y, df["domestic_only"], 0.56, color=C.SERIES[0], edgecolor="white", linewidth=1.2,
            label="Authors in this country only", zorder=3)
    bars = ax.barh(y, df["international"], 0.56, left=df["domestic_only"], color=C.SERIES[1], edgecolor="white",
                   linewidth=1.2, label="Co-authored with another country", zorder=3)
    for bar, row in zip(bars, df.itertuples()):
        ax.annotate(f"{row.papers:,}  ·  {row.international_share_pct:.0f}% international",
                    (bar.get_x() + bar.get_width(), bar.get_y() + bar.get_height() / 2), xytext=(4, 0),
                    textcoords="offset points", ha="left", va="center", fontsize=8, color=C.INK_2)
    ax.set_yticks(y)
    ax.set_yticklabels([short(n) for n in df["country"]])
    ax.invert_yaxis()
    ax.set_xlim(0, df["papers"].max() * 1.30)
    ax.xaxis.set_major_formatter(thousands)
    ax.set_xlabel("RAG papers, 2019–2026")
    C.style_axes(ax, grid="x")
    C.legend(ax, loc="lower right", handlelength=1.0)
    fig.tight_layout()
    total, intl = df["all_papers_with_country"].iloc[0], df["all_international_papers"].iloc[0]
    C.titles(fig, "The 15 largest producers of RAG papers, and how much they collaborate abroad",
             f"Across all countries, {intl:,} of {total:,} papers with a known country ({intl / total:.0%}) involve more than one country.",
             f"{C.SOURCE} {COVERAGE_NOTE}")
    data.save(fig, "fig2_top_countries_collab")


def fig3_country_share_by_year(data: C.Data) -> None:
    df = data.fetch("fig3_country_share_by_year")
    years = sorted(df["year"].unique())
    totals = df.groupby("year")["papers_with_country"].first()
    order = df.drop_duplicates("country_code").sort_values("overall_rank")
    table = df.pivot(index="year", columns="country_code", values="share_pct").reindex(years).fillna(0)
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    ends = []
    for row, color in zip(order.itertuples(), C.SERIES):
        C.line(ax, years, table[row.country_code], color, label=short(row.country), partial_from=2026)
        ends.append((table[row.country_code].iloc[-1], f"{short(row.country)}  {table[row.country_code].iloc[-1]:.0f}%"))
    ax.set_xticks(years)
    ax.set_xticklabels([f"{y}\nn = {totals[y]:,.0f}" for y in years])
    ax.set_xlim(years[0] - 0.2, years[-1] + 0.15)
    ax.set_ylim(0, None)
    ax.yaxis.set_major_formatter(percent)
    ax.set_ylabel("Share of the year's papers with a known country")
    C.style_axes(ax)
    C.legend(ax, loc="upper right", ncol=3, handlelength=1.4, columnspacing=1.4)
    C.end_labels(ax, years[-1], ends)
    fig.tight_layout()
    C.titles(fig, "Share of each year's RAG papers involving the six largest producers",
             "n = papers of that year with a known country. 2023 rests on 39 papers. Hollow marker: 2026 is incomplete.",
             f"{C.SOURCE} {C.PARTIAL_2026}\nA paper with authors in several countries counts for each, so shares can add up to more than 100%.")
    data.save(fig, "fig3_country_share_by_year")


def fig4_collab_matrix(data: C.Data) -> None:
    df = data.fetch("fig4_collab_matrix")
    order = df.drop_duplicates("country_code_a").sort_values("rank_a")
    codes = order["country_code_a"].tolist()
    names = [short(n) for n in order["country_a"]]
    matrix = (df.pivot(index="country_code_a", columns="country_code_b", values="joint_papers")
              .reindex(index=codes, columns=codes).fillna(0).to_numpy())
    lower = np.tril(np.ones_like(matrix, dtype=bool), k=-1)           # each pair once
    shown = np.where(lower, matrix, np.nan)
    norm = PowerNorm(0.5, vmin=0, vmax=matrix.max())
    fig, ax = plt.subplots(figsize=(8.2, 6.6))
    ax.imshow(shown, cmap=C.SEQUENTIAL, norm=norm, aspect="equal")
    for i in range(len(codes)):
        for j in range(i):
            value = matrix[i, j]
            ax.text(j, i, f"{value:.0f}", ha="center", va="center", fontsize=8,
                    color="white" if norm(value) > 0.55 else C.INK)
    ax.set_xticks(range(len(codes) - 1))
    ax.set_xticklabels(names[:-1], rotation=40, ha="right", rotation_mode="anchor")
    ax.set_yticks(range(1, len(codes)))
    ax.set_yticklabels(names[1:])
    ax.set_xlim(-0.5, len(codes) - 1.5)
    ax.set_ylim(len(codes) - 0.5, 0.5)
    # White grid lines between cells: the surface gap that separates touching fills.
    ax.set_xticks(np.arange(-0.5, len(codes) - 1), minor=True)
    ax.set_yticks(np.arange(0.5, len(codes)), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="both", length=0, colors=C.INK_2, labelsize=8.5)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()
    C.titles(fig, "Co-authored RAG papers between the twelve largest producers",
             "Number of papers with authors in both countries, 2019–2026. Darker means more.",
             f"{C.SOURCE} {COVERAGE_NOTE}")
    data.save(fig, "fig4_collab_matrix")


def _arc(p0: np.ndarray, p1: np.ndarray, bend: float = 0.16, steps: int = 60) -> np.ndarray:
    """A gentle curve between two map points, bulging to the north. It is a drawing device,
    not a route: it only has to keep overlapping links apart."""
    mid = (p0 + p1) / 2
    normal = np.array([-(p1 - p0)[1], (p1 - p0)[0]])
    if normal[1] < 0:
        normal = -normal
    control = mid + bend * normal
    t = np.linspace(0, 1, steps)[:, None]
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * control + t ** 2 * p1


def fig5_collab_map(data: C.Data) -> None:
    df = data.fetch("fig5_collab_pairs")
    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    worldmap.draw_base(ax, {}, C.LAND)

    def width(papers: float) -> float:
        return 0.7 + 5.3 * (papers / df["joint_papers"].max()) ** 0.75

    for row in df.sort_values("joint_papers").itertuples():        # thick links last, on top
        p0 = np.array(worldmap.project(row.longitude_a, row.latitude_a))
        p1 = np.array(worldmap.project(row.longitude_b, row.latitude_b))
        curve = _arc(p0, p1)
        ax.plot(curve[:, 0], curve[:, 1], color=C.SERIES[0], lw=width(row.joint_papers), alpha=0.55,
                solid_capstyle="round", zorder=2)

    ends = np.concatenate([df[["country_code_a", "country_a", "longitude_a", "latitude_a", "joint_papers"]].to_numpy(),
                           df[["country_code_b", "country_b", "longitude_b", "latitude_b", "joint_papers"]].to_numpy()])
    nodes = {}
    for code, name, lon, lat, papers in ends:
        entry = nodes.setdefault(code, {"name": name, "lon": lon, "lat": lat, "papers": 0})
        entry["papers"] += papers
    # Label positions (dx, dy, ha) in points, set by hand where capitals crowd each other.
    place = {"GB": (-9, -3, "right"), "NL": (-2, 15, "center"), "DE": (8, 9, "left"), "FR": (-7, -10, "right"),
             "IT": (6, -9, "left"), "CN": (-10, -11, "right"), "KR": (7, 10, "left"), "JP": (8, -2, "left"),
             "HK": (9, -7, "left"), "MO": (-8, -9, "right"), "TW": (9, 5, "left"), "SG": (8, -6, "left"),
             "BD": (-7, -8, "right"), "IN": (-8, 4, "right"), "AU": (8, 0, "left"), "US": (-8, -9, "right"),
             "CA": (-8, 8, "right")}
    for code, node in nodes.items():
        px, py = worldmap.project(node["lon"], node["lat"])
        ax.scatter([px], [py], s=18 + node["papers"] * 0.12, color=C.SERIES[0], edgecolor="white", linewidth=1.2, zorder=3)
        dx, dy, ha = place.get(code, (7, 5, "left"))
        ax.annotate(short(node["name"]), (px, py), xytext=(dx, dy), textcoords="offset points", ha=ha, va="center",
                    fontsize=8, color=C.INK, path_effects=halo, zorder=4)

    keys = [300, 100, 20]
    handles = [Line2D([], [], color=C.SERIES[0], alpha=0.55, lw=width(k), solid_capstyle="round") for k in keys]
    C.legend(ax, handles=handles, labels=[f"{k} joint papers" for k in keys], loc="lower left",
             bbox_to_anchor=(0.01, 0.04), handlelength=2.2, fontsize=8)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    top = df.iloc[0]
    C.titles(fig, "The 30 strongest country-to-country links in RAG research",
             f"Line width: papers with authors in both countries, 2019–2026. Strongest: {short(top['country_a'])}–"
             f"{short(top['country_b'])}, {top['joint_papers']:,} papers. Dot size: a country's total across these links.",
             f"{C.SOURCE} {COVERAGE_NOTE}\nLinks are drawn between capitals as simple curves, not routes. Outlines: Natural Earth 1:110m.")
    data.save(fig, "fig5_collab_map")


def fig6_country_coverage(data: C.Data) -> None:
    by_type = data.fetch("fig6a_coverage_by_type")
    by_year = data.fetch("fig6b_coverage_by_year")
    type_names = {"preprint": "Preprint", "conference-paper": "Conference paper", "article": "Article", "review": "Review"}
    fig, (ax_type, ax_year) = plt.subplots(1, 2, figsize=(10.6, 3.4), gridspec_kw={"width_ratios": [1.25, 1]})

    y = np.arange(len(by_type))
    bars = ax_type.barh(y, by_type["with_country_pct"], 0.5, color=C.SERIES[0], zorder=3)
    C.bar_labels(ax_type, bars, [f"{r.with_country_pct:.0f}%  ({r.with_country:,} of {r.works:,})" for r in by_type.itertuples()],
                 horizontal=True)
    ax_type.set_yticks(y)
    ax_type.set_yticklabels([type_names[t] for t in by_type["type"]])
    ax_type.invert_yaxis()
    ax_type.set_xlim(0, 135)
    ax_type.set_xticks([0, 25, 50, 75, 100])
    ax_type.xaxis.set_major_formatter(percent)
    ax_type.set_title("By publication type", loc="left")
    C.style_axes(ax_type, grid="x")

    x = np.arange(len(by_year))
    bars = ax_year.bar(x, by_year["with_country_pct"], 0.42, color=C.SERIES[0], zorder=3)
    C.bar_labels(ax_year, bars, [f"{v:.0f}%" for v in by_year["with_country_pct"]])
    ax_year.set_xticks(x)
    ax_year.set_xticklabels([f"{r.year}\n{r.works:,} papers" for r in by_year.itertuples()])
    ax_year.set_ylim(0, 100)
    ax_year.yaxis.set_major_formatter(percent)
    ax_year.set_title("By publication year", loc="left")
    C.style_axes(ax_year)

    fig.tight_layout(w_pad=3.0)
    known = by_type["with_country"].sum()
    total = by_type["works"].sum()
    C.titles(fig, "How many RAG papers have a known country at all",
             f"Share of papers with at least one author country: {known:,} of {total:,} overall ({known / total:.0%}). "
             "Every country figure rests on these papers only.",
             f"{C.SOURCE} {C.PARTIAL_2026} A paper has no country when its authorships carry no affiliation text.")
    data.save(fig, "fig6_country_coverage")


def main() -> None:
    data = C.Data(HERE)
    print(HERE.name)
    fig1_world_map(data)
    fig2_top_countries_collab(data)
    fig3_country_share_by_year(data)
    fig4_collab_matrix(data)
    fig5_collab_map(data)
    fig6_country_coverage(data)


if __name__ == "__main__":
    main()
