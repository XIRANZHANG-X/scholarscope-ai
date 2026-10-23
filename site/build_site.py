"""Build the project website: one self-contained HTML file that opens offline with a double-click.

    uv run python site/build_site.py [--from-csv] [--out PATH]

Every number and chart comes from the same saved SQL queries the figure scripts run
(analysis/0X_*/sql/*.sql, country_indicators_analysis/sql/*.sql), executed here against the
project database over a read-only connection. The page is a snapshot of those query results:
re-run this script after the data changes. plotly.js and the chart data are inlined, so the file
needs no server and no internet connection.

--from-csv  build from the CSVs the figure scripts last saved, without a database. For working
            on the page layout only; the footer then says so.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from plotly.offline import get_plotlyjs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ANALYSIS = ROOT / "analysis"
D5 = ROOT / "country_indicators_analysis"
sys.path.insert(0, str(ANALYSIS))
sys.path.insert(0, str(D5))
sys.path.insert(0, str(HERE))
import charts as K  # noqa: E402
import common as C  # noqa: E402
import country_indicators_analysis as CI  # noqa: E402

DATA_END = "22 September 2026"


# ---- data ----------------------------------------------------------------------------------------
class Source:
    """Runs a saved query (database) or reads the CSV the figure scripts saved from it."""

    def __init__(self, from_csv: bool) -> None:
        self.from_csv = from_csv
        self.conn = None if from_csv else C.connect()
        self.queries: list[str] = []

    def get(self, folder: Path, name: str, params: dict | None = None) -> pd.DataFrame:
        self.queries.append(str((folder / "sql" / f"{name}.sql").relative_to(ROOT)).replace("\\", "/"))
        if self.from_csv:
            return pd.read_csv(folder / "output" / f"{name}.csv", keep_default_na=False, na_values=[""])
        return C.query(folder / "sql" / f"{name}.sql", params, self.conn)

    def scalar_row(self, sql_file: Path) -> dict:
        self.queries.append(str(sql_file.relative_to(ROOT)).replace("\\", "/"))
        if self.from_csv:
            return {}
        return C.query(sql_file, None, self.conn).iloc[0].to_dict()


def load(src: Source) -> dict:
    d1, d2, d3, d4 = (ANALYSIS / n for n in ("01_theme_comparison", "02_publication_types",
                                              "03_world_collaboration", "04_subfields"))
    rules_table = pd.read_csv(d4 / "subfield_rules.csv", keep_default_na=False)
    rules = {"rules": json.dumps(rules_table.to_dict("records"))}
    data = {
        "theme_yearly": src.get(d1, "fig1_theme_yearly"),
        "growth": src.get(d1, "fig2_growth_multiple"),
        "phrases": src.get(d1, "fig3_phrase_yearly"),
        "rag_per_llm": src.get(d1, "fig4_rag_per_100_llm"),
        "type_quarter": src.get(d2, "fig1_type_by_quarter"),
        "type_year": src.get(d2, "fig2a_type_share_by_year"),
        "type_theme": src.get(d2, "fig2b_type_mix_by_theme"),
        "citations": src.get(d2, "fig3_citations_by_type"),
        "sources": src.get(d2, "fig4_top_sources_by_type"),
        "countries": src.get(d3, "fig1_papers_by_country"),
        "top_collab": src.get(d3, "fig2_top_countries_collab"),
        "country_year": src.get(d3, "fig3_country_share_by_year"),
        "matrix": src.get(d3, "fig4_collab_matrix"),
        "pairs": src.get(d3, "fig5_collab_pairs"),
        "cov_type": src.get(d3, "fig6a_coverage_by_type"),
        "cov_year": src.get(d3, "fig6b_coverage_by_year"),
        "sizes": src.get(d4, "fig1_subfield_sizes", rules),
        "tagging": src.get(d4, "fig1b_tagging_coverage", rules),
        "sub_year": src.get(d4, "fig2_subfield_share_by_year", rules),
        "cooc": src.get(d4, "fig3_subfield_cooccurrence", rules),
        "sub_change": src.get(d4, "fig4_subfield_share_change", rules),
    }
    counts, indicators, profiles = (src.get(D5, name) for name in CI.QUERIES)
    if "TW" not in set(profiles["country_code"]):
        raise SystemExit("Taiwan is missing from the database: run country_indicators_analysis/sql/00_add_taiwan.sql once.")
    table = CI.build_country_table(counts, indicators, profiles)
    full, sub, _ = CI.samples(table)
    _, _, coefs, out_tot, out_pc = CI.fit_models(sub, full)
    data.update(d5_sub=sub, coefs=coefs, out_tot=out_tot, out_pc=out_pc)
    data["overview"] = src.scalar_row(HERE / "sql" / "overview.sql")
    return data


# ---- page content --------------------------------------------------------------------------------
def chart(cid: str, title: str, fig, note: str = "") -> dict:
    return {"id": cid, "title": title, "json": K.to_json(fig), "note": note}


def build_sections(d: dict) -> tuple[dict, list[dict]]:
    ty = d["theme_yearly"].pivot(index="year", columns="theme", values="works_cs")
    g = d["growth"].pivot(index="year", columns="theme", values="growth_multiple")
    rpl = d["rag_per_llm"][d["rag_per_llm"]["field_scope"] == "cs"].set_index("year")["rag_per_100_llm"]
    ov = d["overview"]
    total = int(ov.get("works", ty["rag"].sum()))
    hero = {
        "total": total,
        "first_year": int(ov.get("first_year", ty.index.min())),
        "first_count": int(ty["rag"].iloc[0]),
        "last_count": int(ty["rag"].iloc[-1]),
        "strip": [
            (f"{int(d['top_collab']['all_papers_with_country'].iloc[0]):,}", "papers with a known author country"),
            (f"{len(d['countries']):,}", "countries and territories with at least one paper"),
            (f"{int(d['tagging']['papers_with_abstract'].iloc[0]):,}", "papers with an abstract, tagged by sub-direction"),
        ],
    }

    # 1 · themes
    peak_year = int(g["rag"].idxmax())
    s1 = {
        "id": "themes", "nav": "Themes",
        "title": "RAG climbed fastest of four AI themes",
        "lead": (f"RAG went from {int(ty.loc[peak_year - 1, 'rag']):,} computer-science papers in {peak_year - 1} "
                 f"to {int(ty.loc[peak_year, 'rag']):,} in {peak_year}: {g.loc[peak_year, 'rag']:.1f}× in one year, "
                 f"the steepest single-year rise among RAG, LLM, Agents and foundation models. Measured against "
                 f"LLM work, RAG went from {rpl.loc[2023]:.1f} papers per 100 LLM papers in 2023 to "
                 f"{rpl.iloc[-1]:.1f} in 2026 so far."),
        "stats": [(f"{g.loc[peak_year, 'rag']:.1f}×", f"RAG growth, {peak_year - 1} to {peak_year}"),
                  (f"{rpl.iloc[-1]:.1f}", "RAG papers per 100 LLM papers, 2026*"),
                  (f"{int(ty.loc[2026, 'rag']):,}", "RAG papers, 1 Jan – 22 Sep 2026")],
        "charts": [chart("d1_theme_yearly", "Papers per year by theme", K.d1_theme_yearly(d["theme_yearly"])),
                   chart("d1_growth_by_theme", "Year-over-year growth by theme, 2022–2025", K.d1_growth_by_theme(d["growth"]),
                         "A ring marks each theme’s peak year."),
                   chart("d1_rag_per_100_llm", "RAG papers per 100 LLM papers", K.d1_rag_per_100_llm(d["rag_per_llm"]))],
        "more": [chart("d1_growth_multiple", "Growth multiple by year and theme", K.d1_growth_multiple(d["growth"]),
                       "Black outline: the highest and lowest theme in each year."),
                 chart("d1_growth_heatmap", "Growth multiple, theme by year", K.d1_growth_heatmap(d["growth"])),
                 chart("d1_phrase_yearly", "Papers per year by search phrase", K.d1_phrase_yearly(d["phrases"]),
                       "“AI agent + LLM agent” adds two phrases, so a paper using both counts twice.")],
        "caveats": ["Theme counts come from OpenAlex searches for each theme’s phrases, restricted to computer science; "
                    "they are probe counts, not the 15,921-paper RAG corpus itself.",
                    "2026* covers 1 January to 22 September, so its line is dashed and its marker hollow."],
    }

    # 2 · publication types
    tyr = d["type_year"].pivot(index="year", columns="type", values="share_pct")
    cit = d["citations"].set_index(["cohort_year", "type"])
    no_venue = d["sources"][d["sources"]["type"] == "conference-paper"].iloc[0]
    s2 = {
        "id": "venues", "nav": "Venues",
        "title": "Half of RAG work appears first as a preprint",
        "lead": (f"Preprints were {tyr.loc[2024, 'preprint']:.0f}% of RAG papers in 2024 and {tyr.loc[2025, 'preprint']:.0f}% in 2025. "
                 f"They are also cited least: {cit.loc[(2024, 'preprint'), 'cited_share_pct']:.0f}% of 2024 preprints have been "
                 f"cited at least once, against {cit.loc[(2024, 'conference-paper'), 'cited_share_pct']:.0f}% of 2024 conference papers. "
                 f"Journal articles collect the most citations per paper."),
        "stats": [(f"{tyr.loc[2024, 'preprint']:.0f}%", "of 2024 papers were preprints"),
                  (f"{cit.loc[(2024, 'preprint'), 'cited_share_pct']:.0f}% vs {cit.loc[(2024, 'conference-paper'), 'cited_share_pct']:.0f}%",
                   "2024 preprints vs conference papers cited at least once"),
                  (f"{cit.loc[(2024, 'article'), 'mean_citations']:.1f}", "mean citations per 2024 journal article")],
        "charts": [chart("d2_type_share_by_year", "Publication type mix by year", K.d2_type_share_by_year(d["type_year"]),
                         "2026* looks preprint-heavy because conference papers of the year are still being indexed."),
                   chart("d2_cited_share", "Share of papers cited at least once", K.d2_cited_share(d["citations"])),
                   chart("d2_mean_citations", "Mean citations per paper", K.d2_mean_citations(d["citations"]))],
        "more": [chart("d2_type_by_quarter", "RAG papers per quarter by publication type", K.d2_type_by_quarter(d["type_quarter"])),
                 chart("d2_type_mix_by_theme", "Publication type mix by research theme", K.d2_type_mix_by_theme(d["type_theme"])),
                 chart("d2_sources_preprint", "Top venues: preprints", K.d2_sources(d["sources"], "preprint")),
                 chart("d2_sources_conference", "Top venues: conference papers", K.d2_sources(d["sources"], "conference-paper"),
                       f"{int(no_venue['works_without_source']):,} of {int(no_venue['works_in_type']):,} conference papers have no venue in OpenAlex."),
                 chart("d2_sources_article", "Top venues: articles", K.d2_sources(d["sources"], "article"),
                       "Zenodo is a repository, not a journal: some uploads are labelled as articles.")],
        "caveats": ["Compare types within one publication year only: 2024 papers have had about a year longer to collect citations than 2025 papers.",
                    "Means are pulled up by a few highly cited papers; the share cited at least once is the steadier measure.",
                    "Publication type is OpenAlex’s label and is noisy (repository uploads tagged as articles)."],
    }

    # 3 · geography
    c = d["countries"]
    tc = d["top_collab"]
    top_pair = d["pairs"].sort_values("rank").iloc[0]
    intl = tc.sort_values("international_share_pct", ascending=False)
    s3 = {
        "id": "geography", "nav": "Geography",
        "title": "China and the US write half the attributed papers",
        "lead": (f"Of {int(tc['all_papers_with_country'].iloc[0]):,} papers with a known author country, China appears on "
                 f"{int(c.iloc[0]['papers']):,} and the United States on {int(c.iloc[1]['papers']):,}; "
                 f"{K.short(c.iloc[2]['country'])} is third with {int(c.iloc[2]['papers']):,}. "
                 f"{K.short(top_pair['country_a'])}–{K.short(top_pair['country_b'])} is the largest partnership "
                 f"({int(top_pair['joint_papers'])} joint papers). Small, open research systems co-author most: "
                 f"{intl.iloc[0]['international_share_pct']:.1f}% of {K.short(intl.iloc[0]['country'])}’s papers involve another country, "
                 f"against {intl.iloc[-1]['international_share_pct']:.1f}% of {K.short(intl.iloc[-1]['country'])}’s."),
        "stats": [(f"{c.iloc[0]['share_of_attributed_pct'] + c.iloc[1]['share_of_attributed_pct']:.0f}%",
                   "of attributed papers involve China or the US"),
                  (f"{int(top_pair['joint_papers'])}", f"{K.short(top_pair['country_a'])}–{K.short(top_pair['country_b'])} joint papers"),
                  (f"{int(tc['all_international_papers'].iloc[0]):,}", "papers co-authored across countries")],
        "charts": [chart("d3_world_map", "RAG papers by country", K.d3_world_map(c),
                         "Territories too small to draw (Singapore, Hong Kong, Macao …) are dots. Hover a country for its count."),
                   chart("d3_international_share", "International co-authorship share, top 15 countries",
                         K.d3_international_share(tc)),
                   chart("d3_collab_map", "The 30 largest country partnerships", K.d3_collab_map(d["pairs"]),
                         "Dark blue: the five largest; line width follows the number of joint papers.")],
        "more": [chart("d3_top_countries", "Top 15 countries by RAG papers", K.d3_top_countries(tc)),
                 chart("d3_country_share_by_year", "Country shares of RAG papers, 2023–2026", K.d3_country_share_by_year(d["country_year"]),
                       "2023 rests on 39 papers with a known country; read the trend from 2024."),
                 chart("d3_collab_matrix", "Who co-authors with whom", K.d3_collab_matrix(d["matrix"]),
                       "Each cell: joint papers as a share of the row country’s papers."),
                 chart("d3_coverage_by_type", "Papers with country information, by publication type", K.d3_coverage_by_type(d["cov_type"])),
                 chart("d3_coverage_by_year", "Papers with country information, by year", K.d3_coverage_by_year(d["cov_year"]))],
        "caveats": ["Country comes from author affiliations (OpenAlex, completed with ROR). A paper counts once for every country on it, so country totals add up to more than the paper count.",
                    "About 41% of papers have no country at all, mostly preprints: only 28% of preprints can be placed, against 92% of conference papers.",
                    "Taiwan is shown as its own area; the map outlines are Natural Earth (public domain)."],
    }

    # 4 · sub-directions
    ch = d["sub_change"].set_index("label")
    up, down = ch["change_pp"].idxmax(), ch["change_pp"].idxmin()
    ev = ch.loc["Evaluation & benchmarks"] if "Evaluation & benchmarks" in ch.index else None
    lead4 = (f"{up} rose from {ch.loc[up, 'share_2024_pct']:.0f}% of RAG papers in 2024 to {ch.loc[up, 'share_2026_pct']:.0f}% in 2026, "
             f"and the share naming it in the title rose too ({ch.loc[up, 'share_title_2024_pct']:.0f}% → {ch.loc[up, 'share_title_2026_pct']:.0f}%). "
             f"{down} fell from {ch.loc[down, 'share_2024_pct']:.0f}% to {ch.loc[down, 'share_2026_pct']:.0f}%.")
    if ev is not None:
        lead4 += (f" Evaluation is mentioned ever more often ({ev['share_2024_pct']:.0f}% → {ev['share_2026_pct']:.0f}%) "
                  f"but is rarely the subject: only {ev['share_title_2026_pct']:.0f}% name it in the title.")
    tag = d["tagging"].iloc[0]
    s4 = {
        "id": "subfields", "nav": "Sub-directions",
        "title": "Agents gain ground; fine-tuning loses it",
        "lead": lead4,
        "stats": [(f"{ch.loc[up, 'change_pp']:+.0f} pp", f"{up}, 2024 to 2026*"),
                  (f"{ch.loc[down, 'change_pp']:+.0f} pp", f"{down}, 2024 to 2026*"),
                  (f"{tag['tagged_pct']:.0f}%", "of papers with an abstract match at least one sub-direction")],
        "charts": [chart("d4_share_shift", "How sub-direction shares shifted, 2024 to 2026", K.d4_share_shift(d["sub_year"]),
                         "Hover a filled dot for the change counting titles only."),
                   chart("d4_sizes_method", "RAG sub-directions: methods and concerns", K.d4_sizes(d["sizes"], "method")),
                   chart("d4_sizes_application", "RAG sub-directions: application domains", K.d4_sizes(d["sizes"], "application"))],
        "more": [chart("d4_cooccurrence", "Co-occurrence of RAG sub-directions", K.d4_cooccurrence(d["cooc"]),
                       "Blue: the two appear together more often than chance; red: less often. Outlined: highest and lowest pair.")],
        "caveats": ["Sub-directions are keyword rules (one regular expression each, analysis/04_subfields/subfield_rules.csv) matched in SQL against title and abstract. A match means the topic is mentioned, not that it is the paper’s subject; the title-only count is the stricter reading.",
                    f"{int(tag['papers_without_abstract']):,} papers without an abstract are left out. A paper can carry several sub-directions."],
    }

    # 5 · country indicators
    co = d["coefs"].set_index(["model", "outcome", "term"])
    main = "NB, ≥5 papers"
    gdp_pc = co.loc[(main, "per_capita", "ln_gdp_pc")]
    rd_pc = co.loc[(main, "per_capita", "ln_rd_pct")]
    sub = d["d5_sub"]
    s5 = {
        "id": "indicators", "nav": "Indicators",
        "title": "Wealth, not R&D share, predicts output per person",
        "lead": (f"Across {len(sub)} countries with at least {CI.MIN_PAPERS} RAG papers, a negative binomial model puts the elasticity "
                 f"of papers per person on GDP per capita at {gdp_pc['coef']:.2f} (95% CI {gdp_pc['ci_low']:.2f} to {gdp_pc['ci_high']:.2f}): "
                 f"10% richer, about {(1.1 ** gdp_pc['coef'] - 1) * 100:.0f}% more papers per person. Once wealth is in the model, "
                 f"R&D spending as a share of GDP adds nothing measurable ({rd_pc['coef']:+.2f}, CI {rd_pc['ci_low']:.2f} to {rd_pc['ci_high']:.2f})."),
        "stats": [(f"{gdp_pc['coef']:.2f}", "elasticity of papers per person on GDP per capita"),
                  (f"{rd_pc['coef']:+.2f}", "R&D share, not distinguishable from zero"),
                  (f"{len(sub)}", f"countries with ≥{CI.MIN_PAPERS} papers and all indicators")],
        "charts": [chart("d5_trend_pc_gdp", "RAG papers per million people vs. GDP per capita", K.d5_trend(sub, "papers_per_m", "gdp_pc", CI.MIN_PAPERS)),
                   chart("d5_coef_pc", "Effect of country indicators on papers per million people",
                         K.d5_coefficients(d["coefs"], "per_capita", CI.TERMS_PC, CI.TERM_LABELS, CI.MIN_PAPERS),
                         "Three estimates of the same model; the blue one is reported."),
                   chart("d5_avp_pc", "RAG papers per million people: actual vs. predicted",
                         K.d5_actual_vs_predicted(d["out_pc"], "papers_per_m", "predicted_per_m"),
                         "Flagged: studentized residual beyond ±2 or Cook’s distance above 4/n.")],
        "more": [chart("d5_trend_pc_rd", "RAG papers per million people vs. R&D spending", K.d5_trend(sub, "papers_per_m", "rd_pct", CI.MIN_PAPERS)),
                 chart("d5_trend_gdp", "RAG papers vs. GDP per capita", K.d5_trend(sub, "papers", "gdp_pc", CI.MIN_PAPERS)),
                 chart("d5_trend_rd", "RAG papers vs. R&D spending", K.d5_trend(sub, "papers", "rd_pct", CI.MIN_PAPERS)),
                 chart("d5_trend_pop", "RAG papers vs. population", K.d5_trend(sub, "papers", "pop_m", CI.MIN_PAPERS)),
                 chart("d5_coef_total", "Effect of country indicators on total papers",
                       K.d5_coefficients(d["coefs"], "total", CI.TERMS_TOTAL, CI.TERM_LABELS, CI.MIN_PAPERS)),
                 chart("d5_avp_total", "Total RAG papers: actual vs. predicted",
                       K.d5_actual_vs_predicted(d["out_tot"], "papers", "predicted_papers"))],
        "caveats": ["One row per country: each World Bank indicator takes its latest available value; time is not modelled.",
                    "Single-indicator trendlines do not control for anything; the model estimates do.",
                    "Taiwan is not in the World Bank data; its indicators were added to the database by hand (sql/00_add_taiwan.sql)."],
    }
    return hero, [s1, s2, s3, s4, s5]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the single-file project website")
    parser.add_argument("--from-csv", action="store_true", help="use the saved CSVs instead of the database")
    parser.add_argument("--out", type=Path, default=HERE / "dist" / "scholarscope.html")
    args = parser.parse_args()
    if not args.from_csv and not C.database_configured():
        raise SystemExit("No project database (.env / scholarscope package). Use --from-csv for a layout-only build.")

    src = Source(args.from_csv)
    data = load(src)
    hero, sections = build_sections(data)
    env = Environment(loader=FileSystemLoader(HERE), autoescape=select_autoescape(["html", "j2"]))
    html = env.get_template("template.html.j2").render(
        hero=hero, sections=sections, plotly_js=get_plotlyjs(), data_end=DATA_END,
        tokens=(HERE / "tokens.css").read_text(encoding="utf-8"),
        built=date.today().isoformat(), from_csv=args.from_csv,
        queries=sorted(set(src.queries)), n_charts=sum(len(s["charts"]) + len(s["more"]) for s in sections))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB, "
          f"{sum(len(s['charts']) + len(s['more']) for s in sections)} charts, "
          f"{'CSV snapshot' if args.from_csv else 'database'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
