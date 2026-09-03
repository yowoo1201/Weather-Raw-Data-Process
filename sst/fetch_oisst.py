"""Fetch newest OISST .nc files into Input/oisst_data/.

Default action: walk monthly directories on NCEI, ensure local has the
single best version for each date. Behavior per-date:

- Local missing → download whatever remote has (final preferred, else prelim)
- Local has only prelim, remote has final → download final, REMOVE local prelim
- Local has both final + prelim → REMOVE the obsolete prelim (final wins)
- Local already has final → skip
- Remote has nothing newer → skip

Months to walk are configurable via --months YYYYMM[,YYYYMM...] (default:
current month and previous month).
"""

import argparse
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
LOCAL = ROOT / "Input" / "oisst_data"

BASE = ("https://www.ncei.noaa.gov/data/sea-surface-temperature-"
        "optimum-interpolation/v2.1/access/avhrr/")
NC_RE = re.compile(r'href="(oisst-avhrr-v02r01\.\d{8}(?:_preliminary)?\.nc)"')


def list_remote(month: str) -> list[str]:
    url = f"{BASE}{month}/"
    print(f"  list {url}")
    with urllib.request.urlopen(url, timeout=30) as r:
        html = r.read().decode("utf-8", errors="ignore")
    return sorted(set(NC_RE.findall(html)))


def date_from_name(name: str) -> str:
    m = re.search(r"(\d{8})", name)
    return m.group(1) if m else ""


def is_prelim(name: str) -> bool:
    return "_preliminary" in name


def fetch(month: str, name: str) -> int:
    url = f"{BASE}{month}/{name}"
    dest = LOCAL / name
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    dest.write_bytes(data)
    return len(data)


def default_months() -> list[str]:
    """Current month plus the two preceding months.

    Three-month window so the monthly comparison block in run_pipeline.py
    has at least two prior full months (plus the running current month) to
    line up against CPC's monthly bulletins. Extend with --months
    YYYYMM,... to backfill further history.
    """
    today = date.today()
    months: list[str] = []
    y, m = today.year, today.month
    for _ in range(3):
        months.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(months))


def fetch_months(months: list[str], prune: bool = False) -> tuple[int, int, int]:
    """Programmatic entry point. Returns (n_downloaded, n_bytes, n_removed).

    When ``prune`` is True, any local .nc file whose YYYYMM is NOT in the
    ``months`` list is deleted. Use for the auto rolling-window default
    (current + 2 prior months) so stale history doesn't accumulate.
    """
    LOCAL.mkdir(parents=True, exist_ok=True)

    if prune:
        months_set = set(months)
        pruned = 0
        for p in sorted(LOCAL.glob("oisst-avhrr-v02r01.*.nc")):
            d = date_from_name(p.name)
            if d and d[:6] not in months_set:
                p.unlink()
                pruned += 1
        if pruned:
            print(f"  pruned {pruned} .nc files outside {sorted(months_set)}")

    # Local index: {date: [filenames]}
    local_by_date: dict[str, list[str]] = {}
    for p in sorted(LOCAL.glob("oisst-avhrr-v02r01.*.nc")):
        d = date_from_name(p.name)
        if d:
            local_by_date.setdefault(d, []).append(p.name)

    n_dl = 0
    n_bytes = 0
    n_removed = 0

    for month in months:
        try:
            remote = list_remote(month)
        except Exception as e:
            print(f"  [warn] {month}: listing failed ({e})", file=sys.stderr)
            continue
        print(f"  {month}: {len(remote)} remote .nc files")

        # Best remote version per date (prefer final over prelim)
        best_remote: dict[str, str] = {}
        for n in remote:
            d = date_from_name(n)
            if not d:
                continue
            cur = best_remote.get(d)
            if cur is None or (is_prelim(cur) and not is_prelim(n)):
                best_remote[d] = n

        for d, name in best_remote.items():
            local_names = local_by_date.get(d, [])
            final_name  = f"oisst-avhrr-v02r01.{d}.nc"
            prelim_name = f"oisst-avhrr-v02r01.{d}_preliminary.nc"

            have_final  = final_name  in local_names
            have_prelim = prelim_name in local_names

            # 1. Already have final locally — clean up any obsolete prelim, skip download.
            if have_final:
                if have_prelim:
                    (LOCAL / prelim_name).unlink()
                    print(f"  - removed obsolete prelim: {prelim_name}")
                    n_removed += 1
                continue

            # 2. Remote has final → download it; remove local prelim if any.
            if name == final_name:
                try:
                    sz = fetch(month, name)
                    print(f"  + {name}  ({sz/1024:.0f} KB)")
                    n_dl += 1
                    n_bytes += sz
                    if have_prelim:
                        (LOCAL / prelim_name).unlink()
                        print(f"  - overwrote prelim with final: {prelim_name}")
                        n_removed += 1
                except Exception as e:
                    print(f"  [warn] {name}: download failed ({e})",
                          file=sys.stderr)
                continue

            # 3. Remote only has prelim. Already-have-prelim → skip; otherwise fetch.
            if name == prelim_name:
                if have_prelim:
                    continue
                try:
                    sz = fetch(month, name)
                    print(f"  + {name}  ({sz/1024:.0f} KB)")
                    n_dl += 1
                    n_bytes += sz
                except Exception as e:
                    print(f"  [warn] {name}: download failed ({e})",
                          file=sys.stderr)

    print(f"\n  done — downloaded {n_dl} files, "
          f"{n_bytes/1024/1024:.1f} MB, removed {n_removed} stale prelim")
    return n_dl, n_bytes, n_removed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", default=",".join(default_months()),
                    help="Comma-separated YYYYMM month dirs to walk")
    ap.add_argument("--no-prune", action="store_true",
                    help="Keep .nc files from months outside the walked set "
                         "(default: prune, so the local mirror stays a rolling "
                         "current + 2 prior months window)")
    args = ap.parse_args()
    months = [m.strip() for m in args.months.split(",") if m.strip()]
    fetch_months(months, prune=not args.no_prune)


if __name__ == "__main__":
    main()
