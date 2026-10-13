"""Build one self-contained zip per analysis direction, ready to send to the teammate working on it.

    uv run python analysis/make_packages.py

Writes data/handoff/<direction>.zip (git-ignored; rebuild after any change). Each zip unpacks to
a single folder with that direction's script, SQL, CSVs, figures and README, plus the usage guide.
Directions 1-4 also get a copy of common.py, so `python make_figures.py` runs from inside the
folder with no repository and no database around it.

Before zipping it checks that every query has its CSV and every folder has figures, so nobody is
handed a direction they cannot redraw.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ANALYSIS = Path(__file__).resolve().parent
REPO = ANALYSIS.parent
OUT = REPO / "data" / "handoff"
GUIDE = ANALYSIS / "使用说明.md"
SKIP = {"__pycache__", ".idea"}
NO_RESULT = {"00_add_taiwan"}          # a one-off INSERT, not a query: it has no CSV


def files_under(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*")
                  if p.is_file() and not SKIP & set(p.relative_to(folder).parts))


def problems(folder: Path) -> list[str]:
    """What would stop a teammate from redrawing this direction from the folder alone."""
    found = []
    for sql in sorted((folder / "sql").glob("*.sql")):
        if sql.stem not in NO_RESULT and not (folder / "output" / f"{sql.stem}.csv").exists():
            found.append(f"no output/{sql.stem}.csv for sql/{sql.name}")
    if not list((folder / "output").glob("*.png")):
        found.append("no figures in output/")
    if not list(folder.glob("*.py")):
        found.append("no script")
    return found


def build(name: str, folder: Path, extras: list[Path]) -> Path:
    target = OUT / f"{name}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files_under(folder):
            archive.write(path, (Path(folder.name) / path.relative_to(folder)).as_posix())
        for path in extras:
            archive.write(path, (Path(folder.name) / path.name).as_posix())
    return target


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    packages = [(d.name, d, [GUIDE, ANALYSIS / "common.py"])
                for d in sorted(ANALYSIS.glob("[0-9][0-9]_*")) if d.is_dir()]
    packages.append(("05_country_indicators_analysis", REPO / "country_indicators_analysis", [GUIDE]))

    failed = False
    for name, folder, extras in packages:
        issues = problems(folder)
        if issues:
            failed = True
            print(f"NOT PACKED  {name}: " + "; ".join(issues))
            continue
        target = build(name, folder, extras)
        files = files_under(folder)
        print(f"{target.relative_to(REPO).as_posix():52} {target.stat().st_size / 1_048_576:5.1f} MB   "
              f"{sum(p.suffix == '.py' for p in files) + sum(p.suffix == '.py' for p in extras)} py, "
              f"{sum(p.suffix == '.csv' for p in files)} csv, {sum(p.suffix == '.png' for p in files)} png, "
              f"{sum(p.suffix == '.sql' for p in files)} sql")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
