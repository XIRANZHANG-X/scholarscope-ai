"""Regenerate every figure of the four directions under analysis/.

    uv run python analysis/run_all.py [--from-csv]

Each direction's make_figures.py also runs on its own. Direction 5 (country indicators) lives
in country_indicators_analysis/ and has its own script.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    scripts = sorted(HERE.glob("[0-9][0-9]_*/make_figures.py"))
    for script in scripts:
        result = subprocess.run([sys.executable, str(script), *sys.argv[1:]])
        if result.returncode:
            print(f"FAILED: {script.relative_to(HERE)}")
            return result.returncode
    print(f"Done: {len(scripts)} directions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
