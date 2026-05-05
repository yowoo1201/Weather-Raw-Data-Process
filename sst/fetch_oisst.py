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
    today = date.today()
    cur = f"{today.year}{today.month:02d}"
    if today.month == 1:
        prev = f"{today.year - 1}12"
    else:
        prev = f"{today.year}{today.month - 1:02d}"
    return [prev, cur]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", default=",".join(default_months()),
                    help="Comma-separated YYYYMM month dirs to walk")
    args = ap.parse_args()
    months = [m.strip() for m in args.months.split(",") if m.strip()]

    LOCAL.mkdir(parents=True, exist_ok=True)

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


if __name__ == "__main__":
    main()
