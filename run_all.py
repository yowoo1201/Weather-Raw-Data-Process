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

ROOT = Path(__file__).resolve().parent


def _venv_python() -> Path | None:
    """Return path to the project's .venv interpreter if it exists, else None."""
    if os.name == "nt":
        p = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        p = ROOT / ".venv" / "bin" / "python"
    return p if p.exists() else None


# Auto-bootstrap into .venv: if we're running under a different interpreter
# (e.g., system Python via plain `python run_all.py`), re-launch ourselves
# with the venv interpreter so dependencies in requirements.txt are
# guaranteed to resolve. Skip when already inside the venv or when no venv
# exists.
#
# We use subprocess.run, not os.execv: on Windows, os.execv mishandles paths
# containing spaces (e.g. "E:\El Nino\Code\..."), splitting the argv on the
# space and producing a mangled second-arg path. subprocess.run quotes the
# command line through CreateProcess and survives spaces correctly.
_VPY = _venv_python()
if _VPY and Path(sys.executable).resolve() != _VPY.resolve():
    print(f"[bootstrap] switching to venv interpreter: {_VPY}")
    rc = subprocess.run(
        [str(_VPY), str(Path(__file__).resolve()), *sys.argv[1:]]
    ).returncode
    sys.exit(rc)

# Force UTF-8 for our own prints; child scripts get PYTHONUTF8=1 via env below.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PIPELINES = {
    "sst":        ROOT / "sst" / "run_pipeline.py",
    "subsurface": ROOT / "subsurface" / "run_subsurface.py",
}


def show_error_popup(title: str, message: str) -> None:
    """Show a modal error dialog. Silent fallback if no GUI / tk available."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(title, message, parent=root)
        root.destroy()
    except Exception:
        pass


def run(name: str, script: Path) -> int:
    if not script.exists():
        msg = f"script not found at {script}"
        print(f"[!] {name}: {msg}", file=sys.stderr)
        show_error_popup(f"{name} failed", msg)
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
    if rc != 0:
        show_error_popup(
            f"{name.upper()} pipeline failed",
            f"Pipeline '{name}' exited with code {rc} after {dt:.1f}s.\n\n"
            f"Script: {script.relative_to(ROOT)}\n\n"
            f"Check the terminal for the traceback / error details.",
        )
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
