"""Run both ENSO pipelines sequentially.

Usage:
    python run_all.py             # run both (SST then Subsurface)
    python run_all.py sst         # run only SST
    python run_all.py subsurface  # run only Subsurface
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# Force UTF-8 for our own prints; child scripts get PYTHONUTF8=1 via env below.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent

PIPELINES = {
    "sst":        ROOT / "sst" / "run_pipeline.py",
    "subsurface": ROOT / "subsurface" / "run_subsurface.py",
}


def run(name: str, script: Path) -> int:
    if not script.exists():
        print(f"[!] {name}: script not found at {script}", file=sys.stderr)
        return 127
    print("=" * 70)
    print(f"▶  {name.upper()}  →  {script.relative_to(ROOT)}")
    print("=" * 70)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    t0 = time.time()
    rc = subprocess.run([sys.executable, str(script)], env=env).returncode
    dt = time.time() - t0
    status = "OK" if rc == 0 else f"FAILED (exit {rc})"
    print(f"\n← {name}: {status}  ({dt:.1f}s)\n")
    return rc


def main():
    args = [a.lower() for a in sys.argv[1:]]
    if not args:
        targets = list(PIPELINES.keys())
    else:
        unknown = [a for a in args if a not in PIPELINES]
        if unknown:
            print(f"Unknown pipeline(s): {unknown}. "
                  f"Choose from: {list(PIPELINES.keys())}", file=sys.stderr)
            sys.exit(2)
        targets = args

    overall = 0
    for name in targets:
        rc = run(name, PIPELINES[name])
        if rc != 0:
            overall = rc
    sys.exit(overall)


if __name__ == "__main__":
    main()
