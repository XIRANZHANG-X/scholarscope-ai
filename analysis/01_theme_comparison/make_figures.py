"""Direction 1: RAG next to the other large-model research themes.

    uv run python analysis/01_theme_comparison/make_figures.py [--from-csv]

Four figures from meta.recall_probes (yearly counts OpenAlex returned for eight search phrases;
only RAG's papers were downloaded, the other themes are counts). See README.md for each figure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter

HERE = Path(__file__).resolve().parent
if not (HERE / "common.py").exists():       # in the repo the shared module sits one level up;
    sys.path.insert(0, str(HERE.parent))    # a folder handed over on its own carries a copy
import common as C  # noqa: E402

# Colour follows the theme in every figure of this direction.
THEMES = {
    "rag": ("RAG", C.SERIES[0]),
    "llm": ("LLM", C.SERIES[1]),
    "agents": ("Agents", C.SERIES[2]),
    "foundation_models": ("Foundation models", C.SERIES[3]),
}
PARTIAL_YEAR = 2026
THEME_NOTE = ("LLM, Agents and Foundation models add up several search phrases, so a paper matching two "
              "is counted twice (upper bounds). RAG is one phrase and exact.")

thousands = FuncFormatter(lambda v, _: f"{v:,.0f}")


def fig1_theme_yearly(data: C.Data) -> None:
    df = data.fetch("fig1_theme_yearly")
    fig, ax = plt.subplots(figsize=(8.6, 4.8))
    ends = []
    for theme, (label, color) in THEMES.items():
        d = df[df["theme"] == theme].sort_values("year")
        C.line(ax, d["year"], d["works_cs"], color, label=label, partial_from=PARTIAL_YEAR)
        last = d.iloc[-1]
        ends.append((last["works_cs"], f"{label}  {last['works_cs']:,.0f}"))
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(thousands)
    ax.set_xticks(sorted(df["year"].unique()))
    ax.set_xlim(2018.7, 2026.2)
    ax.set_ylabel("Papers per year (log scale)")
    C.style_axes(ax)
    C.legend(ax, loc="upper left", ncol=4, handlelength=1.4, columnspacing=1.4, borderaxespad=0.2)
    C.end_labels(ax, 2026, ends)
    fig.tight_layout()
    C.titles(fig, "RAG among the large-model research themes",
             "Computer-science papers per year matching each theme's search phrases. Hollow marker: 2026 is incomplete.",
             f"{C.SOURCE} {C.PARTIAL_2026}\n{THEME_NOTE}")
    data.save(fig, "fig1_theme_yearly")


def fig2_growth_multiple(data: C.Data) -> None:
    df = data.fetch("fig2_growth_multiple")
    df = df[df["year"] >= 2023]
    years = sorted(df["year"].unique())
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    width = 0.13
    for i, (theme, (label, color)) in enumerate(THEMES.items()):
        d = df[df["theme"] == theme].set_index("year").loc[years]
        x = np.arange(len(years)) + (i - 1.5) * (width + 0.03)
        # Bars start at 1x ("no change"), the natural baseline of a growth multiple on a log axis.
        bars = ax.bar(x, d["growth_multiple"] - 1, width, bottom=1, color=color, label=label, zorder=3)
        C.bar_labels(ax, bars, [f"{v:.1f}x" for v in d["growth_multiple"]])
    ax.set_yscale("log")
    ax.set_ylim(1, 30)
    ax.set_yticks([1, 2, 5, 10, 20])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}x"))
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([f"{y - 1} to {y}" for y in years])
    ax.set_ylabel("Papers vs the year before (log scale)")
    C.style_axes(ax)
    C.legend(ax, loc="upper right", ncol=4, handlelength=1.0, columnspacing=1.4)
    fig.tight_layout()
    C.titles(fig, "Year-over-year growth of each theme",
             "How many times more computer-science papers than the year before. 1x means no change.",
             f"{C.SOURCE} Complete years only, so 2026 is left out.\n{THEME_NOTE}")
    data.save(fig, "fig2_growth_multiple")


def fig3_phrase_yearly(data: C.Data) -> None:
    df = data.fetch("fig3_phrase_yearly")
    order = ["rag.retrieval_augmented_generation", "llm.large_language_model", "llm.chatgpt",
             "fm.foundation_model", "agents.ai_agent", "agents.llm_agent", "agents.agentic_ai",
             "fm.pretrained_language_model"]
    fig, axes = plt.subplots(2, 4, figsize=(11.5, 5.6), sharex=True, sharey=True)
    for ax, key in zip(axes.flat, order):
        for other in order:                                   # every other phrase, as grey context
            if other != key:
                o = df[df["query_key"] == other].sort_values("year")
                ax.plot(o["year"], o["works_cs"], color=C.GRID, lw=1.0, zorder=1)
        d = df[df["query_key"] == key].sort_values("year")
        label, color = THEMES[d["theme"].iloc[0]]
        C.line(ax, d["year"], d["works_cs"], color, partial_from=PARTIAL_YEAR, ms=4.5)
        last = d.iloc[-1]
        ax.annotate(f"{last['works_cs']:,.0f}", (last["year"], last["works_cs"]), xytext=(5, 9),
                    textcoords="offset points", ha="right", va="bottom", fontsize=8, color=C.INK_2)
        ax.set_title(f"“{d['phrase'].iloc[0]}”", loc="left", fontsize=9.5)
        ax.text(0.0, 1.0, label, transform=ax.transAxes, fontsize=7.5, color=C.MUTED, va="top",
                ha="left", bbox=dict(facecolor="white", edgecolor="none", pad=1.5))
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(thousands)
        ax.set_xticks([2019, 2021, 2023, 2025])
        C.style_axes(ax)
    for ax in axes[:, 0]:
        ax.set_ylabel("Papers per year (log)")
    fig.tight_layout(w_pad=1.2, h_pad=1.6)
    C.titles(fig, "The eight search phrases one by one",
             "Computer-science papers per year; the other seven phrases in grey. Number: 2026 so far (hollow marker).",
             f"{C.SOURCE} {C.PARTIAL_2026} Each phrase is an exact OpenAlex count of papers whose title or abstract contains it.")
    data.save(fig, "fig3_phrase_yearly")


def fig4_rag_per_100_llm(data: C.Data) -> None:
    df = data.fetch("fig4_rag_per_100_llm")
    scopes = {"cs": ("Computer science", C.SERIES[0]), "all": ("All fields", C.NEUTRAL)}
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    ends = []
    for scope, (label, color) in scopes.items():
        d = df[df["field_scope"] == scope].sort_values("year")
        C.line(ax, d["year"], d["rag_per_100_llm"], color, label=label, partial_from=PARTIAL_YEAR)
        ends.append((d["rag_per_100_llm"].iloc[-1], f"{label}  {d['rag_per_100_llm'].iloc[-1]:.1f}"))
    ax.set_ylim(0, None)
    ax.set_xticks(sorted(df["year"].unique()))
    ax.set_xlim(2021.8, 2026.15)
    ax.set_ylabel("RAG papers per 100 LLM papers")
    C.style_axes(ax)
    C.legend(ax, loc="upper left", handlelength=1.4)
    C.end_labels(ax, 2026, ends)
    fig.tight_layout()
    C.titles(fig, "RAG papers for every 100 “large language model” papers",
             "Ratio of two exact phrase counts per year. Hollow marker: 2026 is incomplete.",
             f"{C.SOURCE} {C.PARTIAL_2026}\nA ratio of two searches, not a share: a RAG paper need not also mention “large language model”.")
    data.save(fig, "fig4_rag_per_100_llm")


def main() -> None:
    data = C.Data(HERE)
    print(HERE.name)
    fig1_theme_yearly(data)
    fig2_growth_multiple(data)
    fig3_phrase_yearly(data)
    fig4_rag_per_100_llm(data)


if __name__ == "__main__":
    main()
