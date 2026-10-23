"""Regenerate every figure: the four directions under analysis/ and direction 5
(country_indicators_analysis/).

    uv run python analysis/run_all.py [--strict | --from-csv]

--strict   query the database and fail if it is not there (use this for the final figures)
--from-csv redraw from the saved query results, without a database (for restyling only)
Each script also runs on its own with the same flags.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIRECTION_5 = HERE.parent / "country_indicators_analysis" / "country_indicators_analysis.py"


def main() -> int:
    scripts = [*sorted(HERE.glob("[0-9][0-9]_*/make_figures.py")), DIRECTION_5]
    for script in scripts:
        print(f"== {script.parent.name}", flush=True)
        # Direction 5 prints full model summaries; keep them out of the way unless it fails.
        quiet = script == DIRECTION_5
        result = subprocess.run([sys.executable, str(script), *sys.argv[1:]],
                                capture_output=quiet, text=True, encoding="utf-8")
        if result.returncode:
            if quiet:
                print(result.stdout[-3000:], result.stderr[-3000:])
            print(f"FAILED: {script.relative_to(HERE.parent)}")
            return result.returncode
        if quiet:
            print("\n".join(line for line in result.stdout.splitlines() if line.startswith("  fig")))
    print(f"Done: {len(scripts)} directions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
