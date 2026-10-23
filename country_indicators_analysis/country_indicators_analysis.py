"""Country indicators vs. RAG paper output: a cross-sectional analysis.

Question: how do a country's economic and research-investment indicators relate to how many
RAG papers it has published (2019-01-01 .. 2026-09-22)? The write-up is README.md.

Design
------
* One row per country. Each indicator takes the country's MOST RECENT non-null value and is
  assumed constant over the whole window; time is not modelled.
* Predictors: GDP per capita (current US$), R&D expenditure (% of GDP), population (size control).
* Negative binomial regression (log link), all predictors in logs, so every slope is an elasticity:
    total papers      papers ~ log(GDP pc) + log(R&D %) + log(pop)
    papers per capita papers ~ log(GDP pc) + log(R&D %), offset log(pop)
  The per-capita model is the total model with the population elasticity fixed at 1.
* Two samples: countries with >= 5 papers (main; outliers come from here) and all countries with
  the indicators, zero-paper ones included (reference).
* An OLS on log(papers), >= 5 sample, is fitted only as a cross-check; it is printed, not reported.

Data source
-----------
Everything is read from the project database (PostgreSQL,
https://github.com/XIRANZHANG-X/scholarscope-ai) by running the three queries in sql/01-03.
Taiwan is not in the World Bank data; sql/00_add_taiwan.sql puts it into the database and must
have been run once before this script (re-running it is harmless).

Run:  python country_indicators_analysis.py [--from-csv]
      Needs the database up (repo README, Route A) and the repo's `.env`.
      --from-csv skips the database and reuses the three query results a database run saved in
      output/01-03_*.csv: for restyling a figure, not for final numbers. A copy of this folder
      away from the repository (no `.env` above it) works that way by itself.
Out:  output/  (figures as PNG, tables as CSV); full model printouts go to the console.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.outliers_influence import variance_inflation_factor

ROOT = Path(__file__).resolve().parent
# The house style (fonts, colours, canvas) is shared with analysis/: in the repository it is
# analysis/common.py; a copy of this folder handed over on its own carries common.py inside.
sys.path.insert(0, str(ROOT if (ROOT / "common.py").exists() else ROOT.parent / "analysis"))
import common as C  # noqa: E402
SQL_DIR = ROOT / "sql"
OUT_DIR = ROOT / "output"

QUERIES = ["01_country_paper_counts", "02_country_indicators", "03_country_profiles"]   # sql/<name>.sql
CSV_ENCODING = "utf-8-sig"      # with a BOM, so Excel shows "≥" and "Türkiye" right on a double-click

MIN_PAPERS = 5
INDICATORS = {
    "NY.GDP.PCAP.CD": "gdp_pc",       # GDP per capita, current US$
    "GB.XPD.RSDV.GD.ZS": "rd_pct",    # R&D expenditure, % of GDP
    "SP.POP.TOTL": "population",
}
TERMS_TOTAL = ["ln_gdp_pc", "ln_rd_pct", "ln_pop"]
TERMS_PC = ["ln_gdp_pc", "ln_rd_pct"]
TERM_LABELS = {"ln_gdp_pc": "GDP per capita", "ln_rd_pct": "R&D % of GDP", "ln_pop": "Population"}


def load_from_db() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run the saved SQL files against the project PostgreSQL database.

    Needs: the database restored per the repo README (docker compose + pg_restore),
    sql/00_add_taiwan.sql run once, `pip install sqlalchemy "psycopg[binary]" python-dotenv`,
    and the repo's `.env` (POSTGRES_USER / _PASSWORD / _DB / _HOST / _PORT).
    """
    import os
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL

    load_dotenv(ROOT.parent / ".env")  # the repo's .env, whatever the working directory is
    # Keep 127.0.0.1: on Windows "localhost" resolves to ::1 first and stalls (repo README).
    # URL.create escapes the password, so one containing @ or / still connects.
    url = URL.create(
        "postgresql+psycopg",
        username=os.getenv("POSTGRES_USER", "scholarscope"),
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "scholarscope"),
    )
    engine = create_engine(url)

    def query(name: str) -> pd.DataFrame:
        frame = pd.read_sql(text((SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")), engine)
        # Keep what the query returned, so the figures can be redrawn later without a database.
        frame.to_csv(OUT_DIR / f"{name}.csv", index=False, encoding=CSV_ENCODING)
        return frame

    counts, indicators, profiles = (query(name) for name in QUERIES)
    if "TW" not in set(profiles["country_code"]):
        raise SystemExit("Taiwan is missing from the database: run sql/00_add_taiwan.sql once first.")
    return counts, indicators, profiles


def load_from_csv() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """The three query results as the last database run saved them (output/01-03_*.csv)."""
    missing = [name for name in QUERIES if not (OUT_DIR / f"{name}.csv").exists()]
    if missing:
        raise SystemExit(f"No saved query results ({', '.join(missing)}): CSVs are not in the repository. "
                         "Run once against the database (see README) before using --from-csv.")
    # keep_default_na=False: "NA" is Namibia's country code, not a missing value.
    counts, indicators, profiles = (pd.read_csv(OUT_DIR / f"{name}.csv", keep_default_na=False, na_values=[""])
                                    for name in QUERIES)
    return counts, indicators, profiles


def build_country_table(counts, indicators, profiles) -> pd.DataFrame:
    """One row per country: latest value (and its year) of each indicator, plus paper count."""
    latest = (indicators.sort_values("year")
              .groupby(["country_code", "indicator_code"]).tail(1))
    values = latest.pivot(index="country_code", columns="indicator_code", values="value")
    years = latest.pivot(index="country_code", columns="indicator_code", values="year")
    values = values.rename(columns=INDICATORS)
    years = years.rename(columns={k: f"{v}_year" for k, v in INDICATORS.items()})

    df = (profiles.set_index("country_code")
          .join(values).join(years)
          .join(counts.set_index("country_code")))
    df["papers"] = df["papers"].fillna(0).astype(int)
    df["pop_m"] = df["population"] / 1e6
    df["papers_per_m"] = df["papers"] / df["pop_m"]
    df["ln_gdp_pc"] = np.log(df["gdp_pc"])
    df["ln_rd_pct"] = np.log(df["rd_pct"])
    df["ln_pop"] = np.log(df["pop_m"])                        # log of population in millions
    df["ln_papers"] = np.log(df["papers"].where(df["papers"] > 0))
    return df


# --------------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------------
def fit_nb(data, terms, per_capita):
    """Negative binomial (NB2). Returns (MLE fit with robust SE, GLM twin for influence measures).

    The GLM uses the MLE's dispersion alpha, so its coefficients are identical; it is only there
    because statsmodels exposes studentized residuals and Cook's distance for GLMs.
    """
    formula = "papers ~ " + " + ".join(terms)
    offset = data["ln_pop"] if per_capita else None
    mle = smf.negativebinomial(formula, data, offset=offset).fit(disp=0, maxiter=500, cov_type="HC0")
    alpha = float(mle.params["alpha"])
    glm = smf.glm(formula, data, offset=offset,
                  family=sm.families.NegativeBinomial(alpha=alpha)).fit()
    return mle, glm


def coef_table(res, model_name) -> pd.DataFrame:
    ci = res.conf_int()
    t = pd.DataFrame({"model": model_name, "term": res.params.index, "coef": res.params.values,
                      "se": res.bse.values, "ci_low": ci.iloc[:, 0].values, "ci_high": ci.iloc[:, 1].values,
                      "p_value": res.pvalues.values})
    return t[t["term"] != "alpha"]


def compare(main, ref, terms) -> pd.DataFrame:
    """NB (>=5) vs NB (all countries), slope by slope.

    z = (b_ge5 - b_all) / sqrt(se_ge5^2 + se_all^2). The >=5 sample is a subset of the full one,
    so the estimates are positively correlated and this z is conservative.
    """
    rows = []
    for term in terms:
        b1, s1 = main.params[term], main.bse[term]
        b2, s2 = ref.params[term], ref.bse[term]
        z = (b1 - b2) / np.hypot(s1, s2)
        rows.append({"term": term, "nb_ge5": b1, "se_ge5": s1, "nb_all": b2, "se_all": s2,
                     "z_diff": z, "p_diff": 2 * stats.norm.sf(abs(z)), "same_sign": np.sign(b1) == np.sign(b2)})
    return pd.DataFrame(rows)


def outlier_table(glm, data, outcome) -> pd.DataFrame:
    """Studentized DEVIANCE residuals: under the NB variance a Pearson residual cannot fall below
    about -1/sqrt(alpha) (~ -1.5 here), so Pearson-based flags would never catch a country with
    too FEW papers. Deviance residuals are close to symmetric. NB scale is fixed at 1."""
    infl = glm.get_influence()
    stud_dev = glm.resid_deviance / np.sqrt(1 - infl.hat_matrix_diag)
    n = int(glm.nobs)
    predicted = glm.fittedvalues                               # expected paper count
    t = pd.DataFrame({
        "outcome": outcome,
        "country": data["name"],
        "region": data["region"],
        "papers": data["papers"],
        "predicted_papers": predicted,
        "papers_per_m": data["papers_per_m"],
        "predicted_per_m": predicted / data["pop_m"],
        "actual_over_predicted": data["papers"] / predicted,
        "gdp_pc": data["gdp_pc"],
        "rd_pct": data["rd_pct"],
        "pop_m": data["pop_m"],
        "stud_resid": stud_dev,
        "cooks_d": infl.cooks_distance[0],
        "leverage": infl.hat_matrix_diag,
    }, index=data.index)
    t["flag_residual"] = t["stud_resid"].abs() > 2
    t["flag_influential"] = t["cooks_d"] > 4 / n
    return t.sort_values("stud_resid", ascending=False)


def elasticity_text(term, b):
    return (f"  {TERM_LABELS[term]:<16} b = {b:+.3f}: +10% -> {(1.10 ** b - 1) * 100:+.1f}% papers; "
            f"x2 -> x{2 ** b:.2f} papers")


def samples(df):
    """(full, sub, dropped): countries with all three indicators; those of them with >= MIN_PAPERS
    papers (the main sample); countries with enough papers but a missing indicator."""
    have_ind = df[["gdp_pc", "rd_pct", "population"]].notna().all(axis=1)
    full = df[have_ind].copy()
    sub = full[full["papers"] >= MIN_PAPERS].copy()
    dropped = df[(df["papers"] >= MIN_PAPERS) & ~have_ind]
    return full, sub, dropped


def fit_models(sub, full):
    """Every model the write-up reports: NB on both samples, the OLS cross-check, the coefficient
    table and the outlier tables of the main NB fits. Shared with the website build."""
    fits = {}
    for sample_name, data in [("ge5", sub), ("all", full)]:
        fits[("total", sample_name)] = fit_nb(data, TERMS_TOTAL, per_capita=False)
        fits[("per_capita", sample_name)] = fit_nb(data, TERMS_PC, per_capita=True)
    ols = {"total": smf.ols("ln_papers ~ " + " + ".join(TERMS_TOTAL), sub).fit(cov_type="HC3"),
           "per_capita": smf.ols("np.log(papers_per_m) ~ " + " + ".join(TERMS_PC), sub).fit(cov_type="HC3")}
    coefs = pd.concat([
        coef_table(fits[("total", "ge5")][0], "NB, ≥5 papers").assign(outcome="total"),
        coef_table(fits[("total", "all")][0], "NB, all countries").assign(outcome="total"),
        coef_table(ols["total"], "OLS log, ≥5 papers").assign(outcome="total"),
        coef_table(fits[("per_capita", "ge5")][0], "NB, ≥5 papers").assign(outcome="per_capita"),
        coef_table(fits[("per_capita", "all")][0], "NB, all countries").assign(outcome="per_capita"),
        coef_table(ols["per_capita"], "OLS log, ≥5 papers").assign(outcome="per_capita"),
    ])
    coefs = coefs[coefs["term"] != "Intercept"]
    out_tot = outlier_table(fits[("total", "ge5")][1], sub, "total")
    out_pc = outlier_table(fits[("per_capita", "ge5")][1], sub, "per_capita")
    return fits, ols, coefs, out_tot, out_pc


# --------------------------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------------------------
SHORT_NAMES = {"Hong Kong SAR, China": "Hong Kong", "Macao SAR, China": "Macao",
               "Syrian Arab Republic": "Syria", "Yemen, Rep.": "Yemen", "Korea, Rep.": "South Korea",
               "Egypt, Arab Rep.": "Egypt", "Iran, Islamic Rep.": "Iran", "Viet Nam": "Vietnam",
               "Turkiye": "Türkiye"}
# One plot per figure (house style): an indicator on the x axis, an outcome on the y axis.
X_AXES = {"gdp_pc": ("GDP per Capita", "GDP per capita (US$, log scale)"),
          "rd_pct": ("R&D Spending", "R&D expenditure (% of GDP, log scale)"),
          "pop_m": ("Population", "Population (millions, log scale)")}
Y_AXES = {"papers": ("RAG Papers", "RAG papers (log scale)"),
          "papers_per_m": ("RAG Papers per Million People", "RAG papers per million people (log scale)")}


def label_points(ax, xs, ys, names, all_x, all_y):
    """Name the given countries, each tied to its point by a leader line; labels avoid every
    point on the plot, each other and the legend."""
    C.label_scatter(ax, xs, ys, [SHORT_NAMES.get(n, n) for n in names],
                    avoid=np.column_stack([np.asarray(all_x, float), np.asarray(all_y, float)]))


def fig_trendlines(sub, out_dir) -> pd.DataFrame:
    """Descriptive: one indicator against one outcome per figure, with a power-law trendline
    (a straight line on log-log axes). Five figures: papers per million vs population is not
    drawn, as its slope is just (papers vs population) - 1; its row stays in the table."""
    rows = []
    for ycol, (ytitle, ylab) in Y_AXES.items():
        for xcol, (xtitle, xlab) in X_AXES.items():
            lx, ly = np.log(sub[xcol]), np.log(sub[ycol])
            fit = stats.linregress(lx, ly)
            rows.append({"outcome": ycol, "predictor": xcol, "slope": fit.slope,
                         "r2": fit.rvalue ** 2, "p_value": fit.pvalue})
            if (ycol, xcol) == ("papers_per_m", "pop_m"):
                continue
            fig, ax = C.new_figure()
            ax.scatter(sub[xcol], sub[ycol], s=90, color=C.NEUTRAL, edgecolor="white", linewidth=1.2, zorder=3,
                       label=f"Country (n = {len(sub)}, ≥{MIN_PAPERS} papers)")
            grid = np.linspace(lx.min(), lx.max(), 50)
            ax.plot(np.exp(grid), np.exp(fit.intercept + fit.slope * grid), color=C.BLUE, lw=3, zorder=4,
                    label=f"Trendline: slope {fit.slope:+.2f}, R² {fit.rvalue ** 2:.2f}")
            # Named: the three furthest from the trendline, and the two largest values.
            resid = ly - (fit.intercept + fit.slope * lx)
            top = resid.abs().nlargest(3).index.union(sub[ycol].nlargest(2).index)
            ax.set_xscale("log")
            ax.set_yscale("log")
            C.plain_log_ticks(ax)
            ax.set_xlabel(xlab)
            ax.set_ylabel(ylab)
            C.legend(ax, loc="best")
            C.finish(fig, ax, f"{ytitle} vs. {xtitle}")
            label_points(ax, sub.loc[top, xcol], sub.loc[top, ycol], sub.loc[top, "name"], sub[xcol], sub[ycol])
            C.save(fig, out_dir / f"fig1_trend_{ycol}_vs_{xcol}")
    return pd.DataFrame(rows)


def fig_observed_vs_predicted(panels, out_dir):
    """Actual vs predicted, one figure per model (total papers, papers per million)."""
    for name, (title, t, actual, predicted) in zip(("total", "per_capita"), panels):
        flagged = t["flag_residual"] | t["flag_influential"]
        fig, ax = C.new_figure()
        for mask, colour, text in ((~flagged, C.NEUTRAL, "Within expectation"),
                                   (flagged & (t["stud_resid"] > 0), C.UP, "More papers than predicted (flagged)"),
                                   (flagged & (t["stud_resid"] <= 0), C.DOWN, "Fewer papers than predicted (flagged)")):
            ax.scatter(t.loc[mask, predicted], t.loc[mask, actual], s=90, color=colour, edgecolor="white",
                       linewidth=1.2, zorder=3, label=text)
        lo = min(t[predicted].min(), t[actual].min()) * 0.7
        hi = max(t[predicted].max(), t[actual].max()) * 1.4
        ax.plot([lo, hi], [lo, hi], color=C.INK_2, lw=1.5, ls="--", zorder=2, label="Actual = predicted")
        ax.set_xscale("log")
        ax.set_yscale("log")
        C.plain_log_ticks(ax)
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_xlabel("Predicted by the model (log scale)")
        ax.set_ylabel("Actual (log scale)")
        C.legend(ax, loc="upper left")
        C.finish(fig, ax, f"{title}: Actual vs. Predicted")
        f = t[flagged]
        label_points(ax, f[predicted], f[actual], f["country"], t[predicted], t[actual])
        C.save(fig, out_dir / f"fig2_actual_vs_predicted_{name}")


def fig_coefficients(coefs, out_dir):
    """Dot-and-whisker per outcome: NB (>=5) vs NB (all) vs the OLS cross-check."""
    models = ["NB, ≥5 papers", "NB, all countries", "OLS log, ≥5 papers"]
    labels = [f"Negative binomial, ≥{MIN_PAPERS} papers (main)", "Negative binomial, all countries",
              f"OLS on log(papers), ≥{MIN_PAPERS} papers"]
    panels = [("total", "Total Papers", TERMS_TOTAL), ("per_capita", "Papers per Million People", TERMS_PC)]
    for outcome, title, terms in panels:
        fig, ax = C.new_figure()
        for j, m in enumerate(models):
            d = coefs[(coefs["model"] == m) & (coefs["outcome"] == outcome)].set_index("term").loc[terms]
            y = np.arange(len(terms)) + (j - 1) * 0.22
            ax.errorbar(d["coef"], y, xerr=[d["coef"] - d["ci_low"], d["ci_high"] - d["coef"]],
                        fmt="o", ms=10, color=C.SERIES[j], lw=3, capsize=0, label=labels[j])
        ax.axvline(0, color=C.INK_2, lw=1)
        ax.set_yticks(range(len(terms)))
        ax.set_yticklabels([TERM_LABELS[t] for t in terms])
        ax.invert_yaxis()
        ax.set_xlim(-0.6, 1.5)
        ax.set_xlabel("Elasticity (95% confidence interval)")
        C.legend(ax, loc="upper left")
        C.finish(fig, ax, f"Effect of Country Indicators on {title}")
        C.save(fig, out_dir / f"fig3_coefficients_{outcome}")


# --------------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------------
def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to a legacy code page
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser(description="RAG papers vs country indicators")
    parser.add_argument("--from-csv", action="store_true",
                        help="reuse output/01-03_*.csv instead of querying the database")
    parser.add_argument("--strict", action="store_true",
                        help="fail instead of falling back to the CSVs when there is no database")
    args = parser.parse_args()
    from_csv = args.from_csv
    if not from_csv and not (ROOT.parent / ".env").exists():
        if args.strict:
            raise SystemExit("--strict: no project .env found, cannot query the database.")
        print("No project database around this folder: reusing output/01-03_*.csv (same as --from-csv).\n")
        from_csv = True
    counts, indicators, profiles = load_from_csv() if from_csv else load_from_db()
    df = build_country_table(counts, indicators, profiles)

    full, sub, dropped = samples(df)
    df.to_csv(OUT_DIR / "country_table.csv", encoding=CSV_ENCODING)

    print("RAG papers vs country indicators")
    print("=" * 78)
    print(f"Countries with any RAG paper: {(df['papers'] > 0).sum()}   with >= {MIN_PAPERS}: "
          f"{(df['papers'] >= MIN_PAPERS).sum()}")
    print(f"Main sample (>= {MIN_PAPERS} papers, all indicators): n = {len(sub)}")
    print(f"Full sample (all countries with all indicators):   n = {len(full)} "
          f"({(full['papers'] == 0).sum()} with zero papers)")
    print(f"Dropped for missing indicators though >= {MIN_PAPERS} papers: "
          + ", ".join(f"{r.name} ({r.papers})" for r in dropped.sort_values("papers", ascending=False).itertuples()))
    print("Latest R&D year used (main sample): "
          + ", ".join(f"{int(y)}: {n}" for y, n in sub["rd_pct_year"].value_counts().sort_index().items()))
    print("\nCorrelations (logs, main sample)")
    print(sub[["ln_papers", "ln_gdp_pc", "ln_rd_pct", "ln_pop"]].corr().round(2).to_string())
    X = sub[TERMS_TOTAL].assign(const=1.0).values
    print("VIF: " + ", ".join(f"{t} {variance_inflation_factor(X, i):.2f}" for i, t in enumerate(TERMS_TOTAL)))

    trend = fig_trendlines(sub, OUT_DIR)
    trend.to_csv(OUT_DIR / "trendlines_single_indicator.csv", index=False, encoding=CSV_ENCODING)
    print("\nDescriptive single-indicator trendlines (log-log slope)")
    print(trend.round(3).to_string(index=False))

    # --- negative binomial models, OLS cross-check, coefficient and outlier tables
    fits, ols, coefs, out_tot, out_pc = fit_models(sub, full)
    for (outcome, sample_name), (mle, _) in fits.items():
        print("\n" + "=" * 78 + f"\nNB  outcome={outcome}  sample={sample_name}\n" + "=" * 78)
        print(mle.summary())
        terms = TERMS_TOTAL if outcome == "total" else TERMS_PC
        for term in terms:
            print(elasticity_text(term, mle.params[term]))
        if outcome == "total":
            z = (mle.params["ln_pop"] - 1) / mle.bse["ln_pop"]
            print(f"  H0 population elasticity = 1: z = {z:.2f}, p = {2 * stats.norm.sf(abs(z)):.4f}")

    # --- OLS cross-check (not reported; only to confirm it agrees with the NB fits)
    print("\nOLS cross-check (log outcome, >= 5 sample, HC3)")
    for outcome, res in ols.items():
        print(f"  {outcome}: " + ", ".join(f"{k} {v:+.3f}" for k, v in res.params.items() if k != "Intercept")
              + f"   R2 {res.rsquared:.2f}")

    coefs.to_csv(OUT_DIR / "coefficients.csv", index=False, encoding=CSV_ENCODING)
    fig_coefficients(coefs, OUT_DIR)

    cmp = pd.concat([
        compare(fits[("total", "ge5")][0], fits[("total", "all")][0], TERMS_TOTAL).assign(outcome="total"),
        compare(fits[("per_capita", "ge5")][0], fits[("per_capita", "all")][0], TERMS_PC).assign(outcome="per_capita"),
    ])
    cmp.to_csv(OUT_DIR / "comparison_nb_ge5_vs_all.csv", index=False, encoding=CSV_ENCODING)
    print("\nNB (>=5) vs NB (all countries)")
    print(cmp.round(3).to_string(index=False))

    # --- outliers, from the main (>= 5) NB models
    pd.concat([out_tot, out_pc]).round(3).to_csv(OUT_DIR / "outliers.csv", encoding=CSV_ENCODING)
    for label, t in [("total papers", out_tot), ("papers per million", out_pc)]:
        flagged = t[t["flag_residual"] | t["flag_influential"]]
        print(f"\nFlagged, NB >= 5, {label}  (|stud. resid| > 2 or Cook's D > 4/n = {4 / len(t):.3f})")
        print(flagged[["country", "papers", "predicted_papers", "papers_per_m", "predicted_per_m",
                       "actual_over_predicted", "stud_resid", "cooks_d"]].round(3).to_string())

    # sensitivity: refit without the influential countries
    print("\nSensitivity: NB (>= 5) without influential countries (Cook's D > 4/n)")
    for outcome, t, terms in [("total", out_tot, TERMS_TOTAL), ("per_capita", out_pc, TERMS_PC)]:
        keep = sub.drop(index=t.index[t["flag_influential"]])
        refit = fit_nb(keep, terms, per_capita=(outcome == "per_capita"))[0]
        both = pd.DataFrame({"all": fits[(outcome, "ge5")][0].params[terms], "without": refit.params[terms]})
        print(f"  {outcome}: n {len(sub)} -> {len(keep)} (dropped: {', '.join(t.loc[t['flag_influential'], 'country'])})")
        print(both.round(3).to_string())

    fig_observed_vs_predicted([("Total RAG Papers", out_tot, "papers", "predicted_papers"),
                               ("RAG Papers per Million People", out_pc, "papers_per_m", "predicted_per_m")],
                              OUT_DIR)
    print(f"\nOutputs written to {OUT_DIR}")


if __name__ == "__main__":
    main()
