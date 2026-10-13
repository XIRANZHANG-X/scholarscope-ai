"""Shared plumbing for the analysis figures: saved SQL in, CSV and PNG out, one chart style.

Every figure follows the same route, and each step leaves a file a teammate can open:

    sql/<name>.sql   the query (the only way data enters a figure)
    output/<name>.csv   exactly the rows that query returned, i.e. what the figure draws
    output/<name>.png   the figure

Run a direction's `make_figures.py` with no arguments to query the database. Add `--from-csv`
to redraw from the CSVs already in output/ without a database: handy for restyling a chart, but
anything that goes into the deck must be regenerated the default way.

A direction folder also works on its own, away from the repository (make_packages.py puts a copy
of this file inside it). There is no database to reach there, so it redraws from CSV by itself.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import math
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap

REPO = Path(__file__).resolve().parents[1]

# Chart colours: the same reference palette as country_indicators_analysis, light mode.
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e6e5e0", "#c3c2b7"
NEUTRAL = "#a3a29d"                                # context marks: "everything else"
LAND = "#f0efec"                                   # map land with no data; diverging midpoint
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
UP, DOWN = "#2a78d6", "#e34948"                    # diverging poles: more / less
SEQUENTIAL = LinearSegmentedColormap.from_list(    # one hue, light -> dark: magnitude
    "seq_blue", ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#104281", "#0d366b"])
DIVERGING = LinearSegmentedColormap.from_list("div_red_blue", [DOWN, LAND, UP])

SOURCE = "Source: OpenAlex via the project database, collected 2026-09-22."
PARTIAL_2026 = "2026 covers 1 Jan to 22 Sep only."

# Latin faces first, then CJK faces, so a Chinese title falls back to a font that has the glyphs.
# Only faces installed on this machine are listed: matplotlib falls back along `font.family`
# itself (not along `font.sans-serif`), and it warns about every listed face it cannot find.
_WANTED = ["Segoe UI", "Helvetica Neue", "Arial", "Microsoft YaHei", "PingFang SC", "PingFang HK",
           "Hiragino Sans GB", "Heiti TC", "Noto Sans CJK SC", "DejaVu Sans"]
_INSTALLED = {font.name for font in font_manager.fontManager.ttflist}
# The titles are semibold. A fallback face without that weight gets the nearest one, and
# matplotlib would say so once per face on every run; nothing is wrong, so keep that quiet.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

plt.rcParams.update({
    "font.family": [name for name in _WANTED if name in _INSTALLED],
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
})


def database_configured() -> bool:
    """Whether this folder sits inside the project, with its `.env` and Python package in reach.

    A direction folder handed to a teammate on its own does not: it can only redraw from its CSVs.
    """
    return (REPO / ".env").exists() and importlib.util.find_spec("scholarscope") is not None


class Data:
    """Runs this direction's saved queries and keeps each result as a CSV next to the figure."""

    def __init__(self, here: Path) -> None:
        parser = argparse.ArgumentParser(description=f"Figures for {here.name}")
        parser.add_argument("--from-csv", action="store_true",
                            help="redraw from output/*.csv instead of querying the database")
        self.from_csv = parser.parse_args().from_csv
        if not self.from_csv and not database_configured():
            print("  No project database around this folder: redrawing from output/*.csv (same as --from-csv).")
            self.from_csv = True
        self.sql_dir, self.out_dir = here / "sql", here / "output"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            from sqlalchemy import create_engine

            from scholarscope.config import Settings
            from scholarscope.db import sqlalchemy_url

            settings = Settings(_env_file=REPO / ".env")
            # Read-only session: these scripts can never change the project data.
            engine = create_engine(sqlalchemy_url(settings),
                                   connect_args={"options": "-c default_transaction_read_only=on"})
            self._conn = engine.connect()
        return self._conn

    def fetch(self, name: str, params: dict | None = None) -> pd.DataFrame:
        """Result of sql/<name>.sql, saved to output/<name>.csv (or read back from it).

        `params` fills the query's `:name` placeholders.
        """
        csv = self.out_dir / f"{name}.csv"
        if self.from_csv:
            # "NA" is Namibia's country code, not a missing value.
            return pd.read_csv(csv, keep_default_na=False, na_values=[""])
        from sqlalchemy import text

        sql = (self.sql_dir / f"{name}.sql").read_text(encoding="utf-8")
        frame = pd.read_sql(text(sql), self.conn, params=params)
        # utf-8-sig: Excel opens it with the right encoding on a double-click.
        frame.to_csv(csv, index=False, encoding="utf-8-sig")
        return frame

    def save(self, fig, name: str) -> None:
        path = self.out_dir / f"{name}.png"
        fig.savefig(path, dpi=200, bbox_inches="tight")
        plt.close(fig)
        print(f"  {path.relative_to(REPO)}")


def style_axes(ax, grid: str = "y") -> None:
    """Recessive chrome: no top/right spine, hairline solid grid on the value axis only."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(which="both", colors=INK_2, labelsize=8, length=0)
    ax.grid(False)
    if grid:
        ax.grid(True, axis=grid, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.xaxis.label.set_color(INK_2)
    ax.yaxis.label.set_color(INK_2)
    ax.title.set_color(INK)


def titles(fig, title: str, subtitle: str = "", note: str = "") -> None:
    """Left-aligned title and subtitle above the plot, source note below.

    Call after the layout is final (tight_layout / subplots_adjust): the text is placed relative
    to the axes and `Data.save` crops to whatever is drawn.
    """
    axes = fig.get_axes()
    to_fig = fig.transFigure.inverted()
    boxes = [ax.get_tightbbox().transformed(to_fig) for ax in axes]
    left = min(ax.get_position().x0 for ax in axes)
    right = max(box.x1 for box in boxes)            # includes labels drawn past the axes
    top = max(box.y1 for box in boxes)              # includes panel titles
    bottom = min([box.y0 for box in boxes]          # the note goes under a legend_below(), if any
                 + [box.get_window_extent().transformed(to_fig).y0 for box in fig.legends])
    height = fig.get_figheight()
    room = (right - left) * fig.get_figwidth() * fig.dpi       # pixels the text may span
    lines = 0
    if subtitle:
        text = fig.text(left, top + 0.20 / height, subtitle, fontsize=9, color=INK_2, ha="left", va="bottom")
        lines = _fit(fig, text, room)
    lift = (0.30 + 0.19 * lines if subtitle else 0.22) / height
    fig.text(left, top + lift, title, fontsize=12, color=INK, weight="semibold", ha="left", va="bottom")
    if note:
        text = fig.text(left, bottom - 0.12 / height, note, fontsize=7.5, color=MUTED, ha="left", va="top")
        _fit(fig, text, room)


def _fit(fig, artist, room: float) -> int:
    """Re-wrap `artist` so no line is wider than `room` pixels; returns the number of lines.

    A sentence that fits stays on one line. One that does not is split into lines of about equal
    length rather than filled greedily, so it never ends in a one-word last line. Widths are
    measured, not guessed from the character count, so wide (Chinese) text wraps correctly too.
    """
    renderer = fig.canvas.get_renderer()
    wrapped = []
    for part in artist.get_text().split("\n"):
        artist.set_text(part)
        width = artist.get_window_extent(renderer).width
        if width > room:
            pieces = math.ceil(width / room)
            chars = math.ceil(len(part) / pieces) + 4
            while chars > 20:
                artist.set_text(textwrap.fill(part, chars))
                if artist.get_window_extent(renderer).width <= room:
                    break
                chars -= 2
            part = artist.get_text()
        wrapped.append(part)
    artist.set_text("\n".join(wrapped))
    return sum(part.count("\n") + 1 for part in wrapped)


def legend(ax_or_fig, **kwargs):
    """Frameless legend in secondary ink; identity comes from the swatch, not coloured text."""
    kwargs = {"frameon": False, "fontsize": 8.5, "labelcolor": INK_2} | kwargs
    return ax_or_fig.legend(**kwargs)


def legend_below(fig, handles, labels, **kwargs):
    """One legend for all panels, left-aligned under the plots. Call before `titles`."""
    axes = fig.get_axes()
    to_fig = fig.transFigure.inverted()
    left = min(ax.get_position().x0 for ax in axes)
    bottom = min(ax.get_tightbbox().transformed(to_fig).y0 for ax in axes)
    fig.canvas.draw()
    return legend(fig, handles=handles, labels=labels, loc="upper left", borderaxespad=0,
                  bbox_to_anchor=(left, bottom - 0.10 / fig.get_figheight()), **kwargs)


def line(ax, x, y, color, label=None, partial_from=None, lw=2.0, ms=5.5, zorder=3):
    """A 2px line with ringed markers. Points at or after `partial_from` (an incomplete period)
    get a hollow marker, so the last value is not read as a finished total."""
    x, y = list(x), list(y)
    ax.plot(x, y, color=color, lw=lw, solid_capstyle="round", solid_joinstyle="round",
            label=label, zorder=zorder)
    for xi, yi in zip(x, y):
        hollow = partial_from is not None and xi >= partial_from
        ax.plot([xi], [yi], marker="o", ms=ms, zorder=zorder + 1,
                markerfacecolor="white" if hollow else color,
                markeredgecolor=color if hollow else "white",
                markeredgewidth=1.6 if hollow else 1.2)


def end_labels(ax, x, entries, gap=11.0, dx=7.0, fontsize=8.5) -> None:
    """Direct labels at the right end of lines: `entries` is [(y, text), ...] at data position x.

    Labels that would overlap are pushed apart symmetrically and tied back to their line end with
    a hairline leader, so a label never sits on a neighbour's line unannounced.
    Call once the axis limits and scales are final.
    """
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
        ax.annotate(text, (x, y), xytext=(dx + (7 if nudged else 0), shift), textcoords="offset points",
                    ha="left", va="center", fontsize=fontsize, color=INK_2, annotation_clip=False,
                    arrowprops=dict(arrowstyle="-", color=AXIS, lw=0.7, shrinkA=0, shrinkB=3) if nudged else None)


def bar_labels(ax, bars, texts, horizontal=False, fontsize=8, pad=3) -> None:
    """Value at the tip of each bar, in ink (never in the bar's own colour)."""
    for bar, text in zip(bars, texts):
        if horizontal:
            ax.annotate(text, (bar.get_width(), bar.get_y() + bar.get_height() / 2), xytext=(pad, 0),
                        textcoords="offset points", ha="left", va="center", fontsize=fontsize, color=INK_2)
        else:
            ax.annotate(text, (bar.get_x() + bar.get_width() / 2, bar.get_y() + bar.get_height()),
                        xytext=(0, pad), textcoords="offset points", ha="center", va="bottom",
                        fontsize=fontsize, color=INK_2)
