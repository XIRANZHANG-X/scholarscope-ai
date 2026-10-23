"""Shared plumbing and the one house style for every analysis figure.

Every figure follows the same route, and each step leaves a file anyone can open:

    sql/<name>.sql      the query (the only way data enters a figure)
    output/<name>.csv   exactly the rows that query returned, i.e. what the figure draws
    output/<fig>.png    the figure (and a .pdf twin)

Run a direction's script with no arguments to query the database. Add `--from-csv` to redraw from
the CSVs already in output/ without a database: handy for restyling, but final figures must be
regenerated from the database (`--strict` refuses to fall back to CSV). Without a database
configured (no .env / scholarscope package) the scripts redraw from CSV by themselves.

House style (analysis/图表统一规范.md): one plot per figure on a fixed 13.33 x 7.5 in (16:9 slide)
canvas at 300 dpi; everything bold; title 20 pt centred, axis titles 16, ticks 16, data labels and
legends 14; no grid, no subtitle, no footnote, no top/right spine; one palette in which an object
keeps its colour in every figure; highest/lowest marked by a thick black outline plus a label.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap

REPO = Path(__file__).resolve().parents[1]

# ---- canvas and type -------------------------------------------------------------------------
WIDTH, HEIGHT, DPI = 4000 / 300, 7.5, 300       # 16:9 slide (13.33 x 7.5 in), 4000 x 2250 px
TITLE, AXIS_TITLE, TICK, LABEL, LEGEND = 20, 16, 16, 14, 14

# ---- colours ------------------------------------------------------------------------------------
INK, INK_2 = "#0b0b0b", "#333333"
NEUTRAL = "#a3a29d"                            # context marks: "everything else"
LIGHT = "#e6e5e0"                              # background bars, "missing"
LAND = "#f0efec"                               # map land with no data; diverging midpoint
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BLUE, ORANGE, GREEN, YELLOW = SERIES[:4]
# Nature Publishing Group palette (ggsci "npg", 10 colours), extended with five in the same
# journal style (ggsci NEJM/JCO) for charts with up to 15 separate items. NPG's pure red
# #DC0000 is swapped for NEJM pink: it glared next to the rest.
NATURE = ["#E64B35", "#4DBBD5", "#00A087", "#3C5488", "#F39B7F", "#8491B4", "#91D1C2", "#EE4C97",
          "#7E6148", "#B09C85", "#E18727", "#20854E", "#631879", "#EFC000", "#6F99AD"]
DARK_BLUE, LIGHT_BLUE = "#256abf", "#86b6ef"   # one hue, two shades
UP, DOWN = "#2a78d6", "#e34948"                # diverging poles: more / less
HIGHLIGHT = INK                                # highest / lowest: thick black outline + label
SEQUENTIAL = LinearSegmentedColormap.from_list(
    "seq_blue", ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#104281", "#0d366b"])
DIVERGING = LinearSegmentedColormap.from_list("div_red_blue", [DOWN, LAND, UP])

# An object keeps its colour in every figure of every direction.
COLORS = {
    "rag": BLUE, "llm": ORANGE, "agents": GREEN, "foundation_models": YELLOW,
    "preprint": BLUE, "conference-paper": ORANGE, "article": GREEN, "review": YELLOW,
}
THEME_LABELS = {"rag": "RAG", "llm": "LLM", "agents": "Agents", "foundation_models": "Foundation models"}
TYPE_LABELS = {"preprint": "Preprint", "conference-paper": "Conference paper", "article": "Article",
               "review": "Review"}
PARTIAL_YEAR = 2026                             # runs to 22 Sep: hollow marker + dashed segment

# Latin faces first, then CJK faces as a fallback. Only faces installed here are listed, since
# matplotlib warns about every listed face it cannot find.
_WANTED = ["Segoe UI", "Helvetica Neue", "Arial", "Microsoft YaHei", "PingFang SC", "PingFang HK",
           "Hiragino Sans GB", "Noto Sans CJK SC", "DejaVu Sans"]
_INSTALLED = {font.name for font in font_manager.fontManager.ttflist}
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

plt.rcParams.update({
    "font.family": [name for name in _WANTED if name in _INSTALLED],
    "font.weight": "bold",
    "font.size": LABEL,
    "figure.titlesize": TITLE, "figure.titleweight": "bold",
    "axes.titlesize": AXIS_TITLE, "axes.titleweight": "bold",
    "axes.labelsize": AXIS_TITLE, "axes.labelweight": "bold", "axes.labelcolor": INK,
    "xtick.labelsize": TICK, "ytick.labelsize": TICK,
    "xtick.color": INK_2, "ytick.color": INK_2,
    "xtick.major.size": 0, "ytick.major.size": 0, "xtick.minor.size": 0, "ytick.minor.size": 0,
    "xtick.major.pad": 6, "ytick.major.pad": 6,
    "legend.fontsize": LEGEND, "legend.frameon": False, "legend.title_fontsize": LEGEND,
    "axes.grid": False, "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": INK_2, "axes.linewidth": 1.0,
    "lines.linewidth": 3.0, "lines.markersize": 9,
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
    "text.color": INK,
})


# ---- data ---------------------------------------------------------------------------------------
def database_configured() -> bool:
    """Whether this folder sits inside the project, with its `.env` and Python package in reach."""
    return (REPO / ".env").exists() and importlib.util.find_spec("scholarscope") is not None


_ENGINE = None


def connect():
    """A read-only connection to the project database (these scripts can never change the data)."""
    global _ENGINE
    if _ENGINE is None:
        from sqlalchemy import create_engine

        from scholarscope.config import Settings
        from scholarscope.db import sqlalchemy_url

        settings = Settings(_env_file=REPO / ".env")
        _ENGINE = create_engine(sqlalchemy_url(settings),
                                connect_args={"options": "-c default_transaction_read_only=on"})
    return _ENGINE.connect()


def query(sql_file: Path, params: dict | None = None, conn=None) -> pd.DataFrame:
    """Run one saved .sql file and return the result; writes nothing. Shared by the figure
    scripts and the website build, so both show numbers from the same queries."""
    from sqlalchemy import text

    sql = Path(sql_file).read_text(encoding="utf-8")
    if conn is not None:
        return pd.read_sql(text(sql), conn, params=params)
    with connect() as own:
        return pd.read_sql(text(sql), own, params=params)


class Data:
    """Runs this direction's saved queries, keeps each result as a CSV, and saves the figures."""

    def __init__(self, here: Path) -> None:
        parser = argparse.ArgumentParser(description=f"Figures for {here.name}")
        parser.add_argument("--from-csv", action="store_true",
                            help="redraw from output/*.csv instead of querying the database")
        parser.add_argument("--strict", action="store_true",
                            help="fail instead of falling back to output/*.csv when there is no database")
        args = parser.parse_args()
        self.from_csv = args.from_csv
        if not self.from_csv and not database_configured():
            if args.strict:
                raise SystemExit("  --strict: no project database (.env / scholarscope package) found.")
            print("  No project database around this folder: redrawing from output/*.csv (same as --from-csv).")
            self.from_csv = True
        self.sql_dir, self.out_dir = here / "sql", here / "output"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        # Figures are rebuilt from scratch, so a figure that no longer exists cannot linger.
        for old in [*self.out_dir.glob("*.png"), *self.out_dir.glob("*.pdf")]:
            old.unlink()
        self._conn = None
        self._cache: dict[str, pd.DataFrame] = {}

    @property
    def conn(self):
        if self._conn is None:
            self._conn = connect()
        return self._conn

    def fetch(self, name: str, params: dict | None = None) -> pd.DataFrame:
        """Result of sql/<name>.sql, saved to output/<name>.csv (or read back from it).

        `params` fills the query's `:name` placeholders. Each query runs once per script.
        """
        if name not in self._cache:
            csv = self.out_dir / f"{name}.csv"
            if self.from_csv:
                # "NA" is Namibia's country code, not a missing value.
                frame = pd.read_csv(csv, keep_default_na=False, na_values=[""])
            else:
                frame = query(self.sql_dir / f"{name}.sql", params, self.conn)
                # utf-8-sig: Excel opens it with the right encoding on a double-click.
                frame.to_csv(csv, index=False, encoding="utf-8-sig")
            self._cache[name] = frame
        return self._cache[name].copy()

    def save(self, fig, name: str) -> None:
        save(fig, self.out_dir / name)


# ---- figure -------------------------------------------------------------------------------------
def new_figure(width: float = WIDTH, height: float = HEIGHT):
    """One plot on the standard canvas. The layout engine keeps labels inside the canvas."""
    fig, ax = plt.subplots(figsize=(width, height), layout="constrained")
    fig.get_layout_engine().set(w_pad=0.25, h_pad=0.25)
    return fig, ax


def finish(fig, ax, title: str, grid: bool = False) -> None:
    """Centred bold title; no grid; only the left and bottom axis lines."""
    fig.suptitle(title)
    ax.grid(False)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def save(fig, path: Path) -> None:
    """300-dpi PNG and a PDF twin, on the fixed canvas (never cropped, so every figure matches)."""
    path = Path(path)
    fig.savefig(path.with_suffix(".png"), dpi=DPI)
    fig.savefig(path.with_suffix(".pdf"))
    plt.close(fig)
    print(f"  {path.with_suffix('.png').name}")


def legend(ax, **kwargs):
    return ax.legend(**({"frameon": False, "fontsize": LEGEND} | kwargs))


def label(ax, x, y, text, dx=0, dy=8, ha="center", va="bottom", color=INK_2, **kwargs):
    """A data label: 14 pt bold, offset in points from (x, y)."""
    return ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va,
                       fontsize=LABEL, weight="bold", color=color, annotation_clip=False, **kwargs)


def bar_labels(ax, bars, texts, horizontal=False, pad=5, inside_color=None) -> None:
    """Value at the tip of each bar (or centred inside it when `inside_color` is given)."""
    for bar, text in zip(bars, texts):
        x0, y0, w, h = bar.get_x(), bar.get_y(), bar.get_width(), bar.get_height()
        if inside_color:
            ax.text(x0 + w / 2, y0 + h / 2, text, ha="center", va="center", fontsize=LABEL,
                    weight="bold", color=inside_color)
        elif horizontal:
            label(ax, x0 + w, y0 + h / 2, text, dx=pad, dy=0, ha="left", va="center")
        else:
            label(ax, x0 + w / 2, y0 + h, text, dx=0, dy=pad)


def line(ax, x, y, color, label_text=None, marker="o", lw=3.0, ms=9, zorder=3,
         partial_from=PARTIAL_YEAR) -> None:
    """A line with ringed markers. Points at or after `partial_from` (2026, an incomplete year)
    get a hollow marker and a dashed segment, so they are not read as a finished total."""
    x, y = list(x), list(y)
    solid = [i for i, xi in enumerate(x) if partial_from is None or xi < partial_from]
    ax.plot([x[i] for i in solid], [y[i] for i in solid], color=color, lw=lw,
            solid_capstyle="round", zorder=zorder)
    if solid and len(solid) < len(x):
        k = solid[-1]
        ax.plot(x[k:], y[k:], color=color, lw=lw, ls=(0, (2.5, 2)), zorder=zorder)
    for xi, yi in zip(x, y):
        hollow = partial_from is not None and xi >= partial_from
        ax.plot([xi], [yi], marker=marker, ms=ms, zorder=zorder + 1,
                markerfacecolor="white" if hollow else color,
                markeredgecolor=color if hollow else "white", markeredgewidth=2.0)
    if label_text:
        ax.plot([], [], color=color, lw=lw, marker=marker, ms=ms, markeredgecolor="white", label=label_text)


def highlight_point(ax, x, y, text, dx=0, dy=16, ha="center", va="bottom") -> None:
    """Highest / lowest point: a thick black ring and a bold black label."""
    ax.plot([x], [y], marker="o", ms=20, markerfacecolor="none", markeredgecolor=HIGHLIGHT,
            markeredgewidth=3, zorder=8)
    if text:
        label(ax, x, y, text, dx=dx, dy=dy, ha=ha, va=va, color=HIGHLIGHT, zorder=9)


def highlight_bar(bar) -> None:
    """Highest / lowest bar: a thick black outline."""
    bar.set_edgecolor(HIGHLIGHT)
    bar.set_linewidth(3)
    bar.set_zorder(bar.get_zorder() + 1)


def end_labels(ax, x, entries, gap=20.0, dx=10.0) -> None:
    """Direct labels at the right end of lines: `entries` is [(y, text), ...] at data position x.
    Labels that would overlap are pushed apart and tied back to their line end with a leader."""
    fig = ax.figure
    fig.canvas.draw()
    px = fig.dpi / 72.0
    items = sorted((ax.transData.transform((x, y))[1], y, text) for y, text in entries)
    pos = [p for p, _, _ in items]
    for _ in range(200):
        moved = False
        for i in range(len(pos) - 1):
            short = gap * px - (pos[i + 1] - pos[i])
            if short > 0.01:
                pos[i] -= short / 2
                pos[i + 1] += short / 2
                moved = True
        if not moved:
            break
    for (origin, y, text), target in zip(items, pos):
        shift = (target - origin) / px
        nudged = abs(shift) > 0.5
        ax.annotate(text, (x, y), xytext=(dx + (10 if nudged else 0), shift), textcoords="offset points",
                    ha="left", va="center", fontsize=LABEL, weight="bold", color=INK_2,
                    annotation_clip=False,
                    arrowprops=dict(arrowstyle="-", color=NEUTRAL, lw=1, shrinkA=0, shrinkB=4) if nudged else None)


def label_scatter(ax, xs, ys, names, avoid=None, color=INK) -> None:
    """Name scatter points with a thin leader line to each, placing every label where it covers
    no other label, no point in `avoid` (default: the labelled points) and not the legend.

    Call after the legend and axis limits are set and before saving: it draws the figure once to
    measure, then keeps the labels out of the layout so the axes do not shift.
    """
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    px_per_pt = fig.dpi / 72
    points = ax.transData.transform(np.column_stack([np.asarray(xs, float), np.asarray(ys, float)]))
    obstacles = points if avoid is None else ax.transData.transform(np.asarray(avoid, float))
    frame = ax.get_window_extent(renderer)
    taken = []
    if ax.get_legend() is not None:
        b = ax.get_legend().get_window_extent(renderer)
        taken.append((b.x0, b.y0, b.x1, b.y1))
    # Candidate offsets (points): diagonals first, then sideways, then up/down, at growing radii.
    directions = [(1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)]
    candidates = [(r * dx / np.hypot(dx, dy), r * dy / np.hypot(dx, dy))
                  for r in (22, 38, 58, 80) for dx, dy in directions]
    pad = 4 * px_per_pt
    for (px, py), name in sorted(zip(points, names), key=lambda p: -p[0][1]):
        probe = ax.text(0, 0, name, fontsize=LABEL, weight="bold")
        bb = probe.get_window_extent(renderer)
        probe.remove()
        w, h = bb.width, bb.height
        best, best_score = None, None
        for dx, dy in candidates:
            ax_, ay_ = px + dx * px_per_pt, py + dy * px_per_pt
            x0 = ax_ if dx > 0 else ax_ - w if dx < 0 else ax_ - w / 2
            y0 = ay_ if dy > 0 else ay_ - h if dy < 0 else ay_ - h / 2
            box = (x0 - pad, y0 - pad, x0 + w + pad, y0 + h + pad)
            if box[0] < frame.x0 or box[2] > frame.x1 or box[1] < frame.y0 or box[3] > frame.y1:
                continue
            if any(box[0] < t[2] and box[2] > t[0] and box[1] < t[3] and box[3] > t[1] for t in taken):
                continue
            hits = int(((obstacles[:, 0] > box[0]) & (obstacles[:, 0] < box[2])
                        & (obstacles[:, 1] > box[1]) & (obstacles[:, 1] < box[3])).sum())
            score = hits * 1000 + np.hypot(dx, dy)          # prefer no covered points, then short lines
            if best_score is None or score < best_score:
                best, best_score = (dx, dy, box), score
        if best is None:                                     # nowhere free: fall back to up-right
            best = (22, 22, (px, py, px + w, py + h))
        dx, dy, box = best
        taken.append(box)
        ax.annotate(name, (ax.transData.inverted().transform((px, py))), xytext=(dx, dy),
                    textcoords="offset points", fontsize=LABEL, weight="bold", color=color,
                    ha="left" if dx > 0 else "right" if dx < 0 else "center",
                    va="bottom" if dy > 0 else "top" if dy < 0 else "center", zorder=6,
                    arrowprops=dict(arrowstyle="-", color=INK_2, lw=1.2, shrinkA=0, shrinkB=6)
                    ).set_in_layout(False)


def plain_log_ticks(ax) -> None:
    """Log axes labelled 0.1 / 1 / 10 / 1,000 in the bold house font instead of 10^n math text."""
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    fmt = FuncFormatter(lambda v, _: f"{v:,.0f}" if v >= 1 else f"{v:g}")
    for axis in (ax.xaxis, ax.yaxis):
        if axis.get_scale() == "log":
            axis.set_major_locator(LogLocator(base=10))
            axis.set_major_formatter(fmt)
            axis.set_minor_formatter(NullFormatter())
