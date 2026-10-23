"""Direction 3: where RAG research is done and who works with whom.

    uv run python analysis/03_world_collaboration/make_figures.py [--from-csv]

Data: bridge.work_countries and the World Bank country profiles. Countries are counted in full:
a paper with authors in three countries counts once for each. Volume and cross-border share
are separate figures, the top-5 links are emphasised, and coverage is shown as identified vs
unidentified; house style from common.py; one plot per figure.
See README.md for each figure and its caveats.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.patheffects as pe
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
thousands = FuncFormatter(lambda v, _: f"{v:,.0f}")
percent = FuncFormatter(lambda v, _: f"{v:.0f}%")
halo = [pe.withStroke(linewidth=4, foreground="white")]


def short(name: str) -> str:
    return SHORT_NAMES.get(name, name)


def fig1_world_map(data: C.Data) -> None:
    df = data.fetch("fig1_papers_by_country")
    norm = LogNorm(vmin=1, vmax=df["papers"].max())
    colour = {row.country_code: C.SEQUENTIAL(norm(row.papers)) for row in df.itertuples()}
    fig, ax = C.new_figure()
    drawn = worldmap.draw_base(ax, colour, C.LAND)
    # Territories too small for the 1:110m outlines (Hong Kong, Singapore ...) get a dot.
    dots = df[~df["country_code"].isin(drawn) & df["latitude"].notna()]
    x, y = worldmap.project(dots["longitude"], dots["latitude"])
    ax.scatter(x, y, s=70, c=[colour[c] for c in dots["country_code"]], edgecolor="white", linewidth=1.5, zorder=3)
    inside = {"CN": (103, 35), "US": (-99, 39), "IN": (79, 22)}       # (lon, lat) well inside the country
    for row in df[df["country_code"].isin(inside)].itertuples():
        px, py = worldmap.project(*inside[row.country_code])
        ax.text(px, py, f"{short(row.country)}\n{row.papers:,}", ha="center", va="center", fontsize=C.LABEL,
                weight="bold", color="white", path_effects=[pe.withStroke(linewidth=3, foreground="#0d366b")], zorder=4)
    bar = fig.colorbar(ScalarMappable(norm=norm, cmap=C.SEQUENTIAL), cax=ax.inset_axes([0.03, 0.12, 0.25, 0.035]),
                       orientation="horizontal", ticks=[1, 10, 100, 1000])
    bar.ax.xaxis.set_major_formatter(thousands)
    bar.ax.tick_params(which="both", labelsize=C.LABEL, length=0)
    bar.ax.minorticks_off()
    bar.outline.set_visible(False)
    bar.ax.set_title("RAG papers (log scale)", fontsize=C.LEGEND, weight="bold", loc="left", pad=6)
    fig.suptitle("RAG Papers by Country, 2019–2026")
    data.save(fig, "fig1_world_map")


def fig2_top_countries(data: C.Data) -> None:
    df = data.fetch("fig2_top_countries_collab").sort_values("papers", ascending=False)
    names = [short(n) for n in df["country"]]
    y = np.arange(len(df))

    fig, ax = C.new_figure()
    ax.barh(y, df["domestic_only"], 0.62, color=C.BLUE, edgecolor="white", linewidth=1.5,
            label="Authors in one country", zorder=3)
    ax.barh(y, df["international"], 0.62, left=df["domestic_only"], color=C.ORANGE, edgecolor="white",
            linewidth=1.5, label="Co-authored across countries", zorder=3)
    for yi, total in zip(y, df["papers"]):
        C.label(ax, total, yi, f"{total:,}", dx=6, dy=0, ha="left", va="center")
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.invert_yaxis()
    ax.set_xlim(0, df["papers"].max() * 1.15)
    ax.xaxis.set_major_formatter(thousands)
    ax.set_xlabel("RAG papers with a known country")
    C.legend(ax, loc="lower right")
    C.finish(fig, ax, "Top 15 Countries by RAG Papers")
    data.save(fig, "fig2a_top_countries")

    fig, ax = C.new_figure()
    order = np.argsort(-df["international_share_pct"].to_numpy(), kind="stable")   # highest first
    shares = df["international_share_pct"].to_numpy()[order]
    bars = ax.barh(y, shares, 0.62, color=C.NATURE[:len(shares)], zorder=3)
    words = {0: "Highest", len(shares) - 1: "Lowest"}
    C.bar_labels(ax, bars, [f"{v:.1f}%  {words[k]}" if k in words else f"{v:.1f}%" for k, v in enumerate(shares)],
                 horizontal=True)
    for k in words:
        C.highlight_bar(bars[k])
    ax.set_yticks(y)
    ax.set_yticklabels([names[i] for i in order])
    ax.invert_yaxis()
    ax.set_xlim(0, 115)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.xaxis.set_major_formatter(percent)
    ax.set_xlabel("Share of the country's papers co-authored with another country")
    C.finish(fig, ax, "International Co-authorship Share, Top 15 Countries")
    data.save(fig, "fig2b_international_share")


def fig3_country_share_by_year(data: C.Data) -> None:
    df = data.fetch("fig3_country_share_by_year")
    years = sorted(df["year"].unique())
    totals = df.groupby("year")["papers_with_country"].first()
    order = df.drop_duplicates("country_code").sort_values("overall_rank")
    table = df.pivot(index="year", columns="country_code", values="share_pct").reindex(years).fillna(0)
    fig, ax = C.new_figure()
    ends = []
    for row, colour in zip(order.itertuples(), C.SERIES):
        values = table[row.country_code]
        C.line(ax, years, values, colour, label_text=short(row.country))
        ends.append((values.iloc[-1], f"{short(row.country)} {values.iloc[-1]:.0f}%"))
    ax.set_xticks(years)
    ax.set_xticklabels([f"{y}{'*' if y == C.PARTIAL_YEAR else ''}\nn = {totals[y]:,}" for y in years])
    ax.set_xlim(years[0] - 0.2, years[-1] + 0.2)
    ax.set_ylim(0, 60)
    ax.yaxis.set_major_formatter(percent)
    ax.set_xlabel("Year (n = papers with a known country; 2026* to 22 Sep)")
    ax.set_ylabel("Share of the year's papers")
    C.finish(fig, ax, "Country Shares of RAG Papers, 2023–2026")
    C.end_labels(ax, years[-1], ends, gap=22)
    data.save(fig, "fig3_country_share_by_year")


def fig4_collab_matrix(data: C.Data) -> None:
    df = data.fetch("fig4_collab_matrix")
    order = df.drop_duplicates("country_code_a").sort_values("rank_a")
    codes = order["country_code_a"].tolist()
    names = [short(n) for n in order["country_a"]]
    matrix = (df.pivot(index="country_code_a", columns="country_code_b", values="joint_papers")
              .reindex(index=codes, columns=codes).fillna(0).to_numpy())
    shown = np.where(np.tril(np.ones_like(matrix, dtype=bool), k=-1), matrix, np.nan)
    norm = PowerNorm(0.5, vmin=0, vmax=matrix.max())
    fig, ax = C.new_figure()
    image = ax.imshow(shown, cmap=C.SEQUENTIAL, norm=norm, aspect="auto")
    for i in range(len(codes)):
        for j in range(i):
            v = matrix[i, j]
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=C.LABEL, weight="bold",
                    color="white" if norm(v) > 0.55 else C.INK)
    ax.set_xticks(range(len(codes) - 1))
    ax.set_xticklabels(names[:-1], rotation=35, ha="right", rotation_mode="anchor")
    ax.set_yticks(range(1, len(codes)))
    ax.set_yticklabels(names[1:])
    ax.set_xlim(-0.5, len(codes) - 1.5)
    ax.set_ylim(len(codes) - 0.5, 0.5)
    ax.set_xticks(np.arange(-0.5, len(codes) - 1), minor=True)
    ax.set_yticks(np.arange(0.5, len(codes)), minor=True)
    ax.grid(which="minor", color="white", linewidth=3)
    ax.tick_params(which="minor", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    bar = fig.colorbar(image, ax=ax, shrink=0.75, pad=0.02, ticks=[0, 20, 50, 100, 200, 300])
    bar.set_label("Joint papers", fontsize=C.AXIS_TITLE, weight="bold")
    bar.ax.tick_params(labelsize=C.TICK, length=0)
    bar.outline.set_visible(False)
    fig.suptitle("Co-authored Papers Between the Top 12 Countries")
    data.save(fig, "fig4_collab_matrix")


def _arc(p0, p1, bend=0.16, steps=60):
    """A gentle curve between two map points, bulging north: a drawing device, not a route."""
    middle = (p0 + p1) / 2
    normal = np.array([-(p1 - p0)[1], (p1 - p0)[0]])
    if normal[1] < 0:
        normal = -normal
    control = middle + bend * normal
    t = np.linspace(0, 1, steps)[:, None]
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * control + t ** 2 * p1


def fig5_collab_map(data: C.Data) -> None:
    df = data.fetch("fig5_collab_pairs").sort_values("rank")
    top = set(df.head(5)["rank"])
    fig, ax = C.new_figure()
    worldmap.draw_base(ax, {}, C.LAND)
    limits = ax.get_xlim(), ax.get_ylim()

    def width(papers):
        return 1.0 + 6.0 * (papers / df["joint_papers"].max()) ** 0.75

    for emphasis in (False, True):                      # the five strongest links on top
        for row in df.itertuples():
            if (row.rank in top) != emphasis:
                continue
            p0 = np.array(worldmap.project(row.longitude_a, row.latitude_a))
            p1 = np.array(worldmap.project(row.longitude_b, row.latitude_b))
            curve = _arc(p0, p1)
            ax.plot(curve[:, 0], curve[:, 1], lw=width(row.joint_papers),
                    color=C.SEQUENTIAL(0.95) if emphasis else C.LIGHT_BLUE,
                    alpha=0.95 if emphasis else 0.6, solid_capstyle="round", zorder=3 if emphasis else 2)
    nodes = {}
    for side in ("a", "b"):
        for row in df.itertuples():
            nodes[getattr(row, f"country_code_{side}")] = (getattr(row, f"country_{side}"),
                                                           getattr(row, f"longitude_{side}"),
                                                           getattr(row, f"latitude_{side}"))
    # Label offsets (dx, dy, ha) in points, set by hand where capitals crowd each other.
    place = {"GB": (-12, -4, "right"), "NL": (-8, 24, "right"), "DE": (12, 14, "left"), "FR": (-10, -18, "right"),
             "IT": (10, -16, "left"), "CN": (-14, -16, "right"), "KR": (10, 14, "left"), "JP": (12, -2, "left"),
             "HK": (14, -12, "left"), "MO": (-12, -18, "right"), "TW": (14, 8, "left"), "SG": (12, -10, "left"),
             "BD": (-10, -14, "right"), "IN": (-12, 6, "right"), "AU": (12, 0, "left"), "US": (-12, -14, "right"),
             "CA": (-12, 12, "right")}
    for code, (name, lon, lat) in nodes.items():
        px, py = worldmap.project(lon, lat)
        ax.scatter([px], [py], s=80, color=C.DARK_BLUE, edgecolor="white", linewidth=1.5, zorder=4)
        dx, dy, ha = place.get(code, (10, 6, "left"))
        ax.annotate(short(name), (px, py), xytext=(dx, dy), textcoords="offset points", ha=ha, va="center",
                    fontsize=C.LABEL, weight="bold", color=C.INK, path_effects=halo, zorder=5).set_in_layout(False)
    handles = [Line2D([], [], color=C.SEQUENTIAL(0.95), lw=6), Line2D([], [], color=C.LIGHT_BLUE, lw=3)]
    C.legend(ax, handles=handles, labels=["Top 5 links", f"Links 6–{len(df)}"], loc="lower left",
             bbox_to_anchor=(0.01, 0.06), title="Line width = joint papers")
    ax.set_xlim(*limits[0])
    ax.set_ylim(*limits[1])
    fig.suptitle(f"Top {len(df)} International Collaboration Links")
    data.save(fig, "fig5_collab_map")


def fig6_country_coverage(data: C.Data) -> None:
    by_type = data.fetch("fig6a_coverage_by_type").sort_values("with_country_pct", ascending=False)
    by_year = data.fetch("fig6b_coverage_by_year").sort_values("year")

    fig, ax = C.new_figure()
    y = np.arange(len(by_type))
    known = by_type["with_country_pct"].to_numpy()
    ax.barh(y, known, 0.6, color=C.BLUE, zorder=3, label="Country identified")
    ax.barh(y, 100 - known, 0.6, left=known, color=C.LIGHT, zorder=3, label="No country identified")
    for yi, v in zip(y, known):
        ax.text(v / 2, yi, f"{v:.0f}%", ha="center", va="center", fontsize=C.LABEL, weight="bold", color="white")
    ax.set_yticks(y)
    ax.set_yticklabels([f"{C.TYPE_LABELS[r.type]}\n({r.with_country:,} of {r.works:,})" for r in by_type.itertuples()])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(percent)
    ax.set_xlabel("Share of papers")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, frameon=False)
    C.finish(fig, ax, "Papers with Country Information, by Publication Type")
    data.save(fig, "fig6a_coverage_by_type")

    fig, ax = C.new_figure()
    x = np.arange(len(by_year))
    bars = ax.bar(x, by_year["with_country_pct"], 0.55, color=C.BLUE, zorder=3)
    for bar, year in zip(bars, by_year["year"]):
        if year == C.PARTIAL_YEAR:                      # the incomplete year, hatched
            bar.set_facecolor("white")
            bar.set_edgecolor(C.BLUE)
            bar.set_hatch("//")
            bar.set_linewidth(2)
    C.bar_labels(ax, bars, [f"{v:.0f}%" for v in by_year["with_country_pct"]])
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r.year}{'*' if r.year == C.PARTIAL_YEAR else ''}\nn = {r.works:,}"
                        for r in by_year.itertuples()])
    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(percent)
    ax.set_xlabel("Year (n = all papers; 2026* to 22 Sep)")
    ax.set_ylabel("Papers with a known country")
    C.finish(fig, ax, "Papers with Country Information, by Year")
    data.save(fig, "fig6b_coverage_by_year")


def main() -> None:
    data = C.Data(HERE)
    print(HERE.name)
    fig1_world_map(data)
    fig2_top_countries(data)
    fig3_country_share_by_year(data)
    fig4_collab_matrix(data)
    fig5_collab_map(data)
    fig6_country_coverage(data)


if __name__ == "__main__":
    main()
