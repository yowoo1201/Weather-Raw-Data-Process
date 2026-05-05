"""Fetch latest PMEL TAO/TRITON subsurface T-profile data.

Reproduces the manual workflow from
  https://www.pmel.noaa.gov/tao/data_deliv/deliv-nojava-all.html
namely: click All, set Data → Subsurface Temp, Start date → Apr 1 of the
current year, Submit, then download the resulting data.tar.

Steps the CGI runs:
  1. POST/GET cover.cgi with all checkbox values + form params
  2. Server bundles the requested ASCII files into a per-request cache dir
  3. Response HTML contains a link to <cache>/data.tar
  4. Download that file → Input/data.tar (overwrite)

Usage:
  python subsurface/fetch_pmel.py                    # default: Pacific (TAO/TRITON), Apr 1 → Dec 31 current year
  python subsurface/fetch_pmel.py --start 2026-04-01 --end 2026-12-31
  python subsurface/fetch_pmel.py --basin indian     # RAMA → data_rama.tar (uses different form page)
"""

import argparse
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
INPUT_DIR = ROOT / "Input"

PMEL_BASE = "https://www.pmel.noaa.gov"

BASIN_FORMS = {
    # basin → (form-page URL, output filename in Input/, lat/lon defaults)
    "pacific": {
        "form_url": f"{PMEL_BASE}/tao/data_deliv/deliv-nojava-all.html",
        "out_name": "data.tar",
        "minlon": "130", "maxlon": "-95",
        "minlat": "-8",  "maxlat": "12",
    },
    "indian": {
        "form_url": f"{PMEL_BASE}/tao/data_deliv/deliv-nojava-rama.html",
        "out_name": "data_rama.tar",
        "minlon": "40",  "maxlon": "100",
        "minlat": "-16", "maxlat": "25",
    },
}


def _scrape_buoy_checkboxes(html: str) -> list[tuple[str, str]]:
    """Pull every (name, value) pair from <input type=checkbox> in the form."""
    out = []
    for c in re.findall(r"<input[^>]*type=\"?checkbox[^>]*>", html, re.I):
        n = re.search(r"name\s*=\s*\"?([\w]+)", c, re.I)
        v = re.search(r"value\s*=\s*\"?([\w]+)", c, re.I)
        if n and v:
            out.append((n.group(1), v.group(1)))
    return out


def fetch_basin(basin: str = "pacific",
                start: date | None = None,
                end:   date | None = None,
                timeout: int = 300) -> Path:
    """Download the latest TAO/TRITON or RAMA subsurface T-profile tarball.

    Returns the path to the saved tarball under Input/. Raises on failure.
    """
    if basin not in BASIN_FORMS:
        raise ValueError(f"unknown basin: {basin}")
    cfg = BASIN_FORMS[basin]

    # Default window: Apr 1 of current year → Dec 31 of current year (PMEL
    # caps end at server time anyway, so picking Dec 31 just asks "give me
    # everything since Apr 1").
    today = date.today()
    if start is None:
        start = date(today.year, 4, 1)
    if end is None:
        end = date(today.year, 12, 31)

    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Fetch the form HTML to harvest checkbox names.
    print(f"  [pmel/{basin}] fetching form page...")
    with urllib.request.urlopen(cfg["form_url"], timeout=60) as r:
        form_html = r.read().decode("utf-8", errors="ignore")
    buoys = _scrape_buoy_checkboxes(form_html)
    if not buoys:
        raise RuntimeError(f"no buoy checkboxes found in {cfg['form_url']}")
    print(f"  [pmel/{basin}] {len(buoys)} buoy sites (selecting all)")

    # 2. Submit the CGI with: Subsurface Temp, Daily, ASCII, tar-by-site, no compression.
    params = [
        ("P1",  "deliv"),
        ("P2",  "t"),                # Subsurface Temp
        ("P3",  "dy"),               # Daily
        ("P4",  f"{start.year}"),
        ("P5",  f"{start.month}"),
        ("P6",  f"{start.day:02d}"),
        ("P7",  f"{end.year}"),
        ("P8",  f"{end.month}"),
        ("P9",  f"{end.day:02d}"),
        ("P10", "buoytar"),          # files by site in a tar-file
        ("P11", "ascii"),
        ("P12", "None"),             # no compression
        ("P13", "all"),
        ("P14", "anonymous"),
        ("P15", ""),
        ("P16", "anonymous"),
        ("P17", ""),
        ("P18", cfg["minlon"]),
        ("P19", cfg["maxlon"]),
        ("P20", cfg["minlat"]),
        ("P21", cfg["maxlat"]),
        ("p22", "html"),
        ("script", "disdel/nojava.csh"),
    ] + buoys
    qs = urllib.parse.urlencode(params)
    cgi_url = f"{PMEL_BASE}/cgi-tao/cover.cgi?{qs}"

    print(f"  [pmel/{basin}] submitting (timeout={timeout}s)...")
    with urllib.request.urlopen(cgi_url, timeout=timeout) as r:
        resp = r.read().decode("utf-8", errors="ignore")

    # 3. Find the data.tar link in the response.
    m = re.search(
        r"href\s*=\s*\"\s*(/cache-tao/[^\"\s]+/data\.tar)\s*\"", resp, re.I)
    if not m:
        # Surface common server-side errors
        msg = re.search(r"<B>([^<]+)</B>", resp)
        snippet = (msg.group(1).strip() if msg else resp[:300]).strip()
        raise RuntimeError(f"data.tar link not found in response: {snippet}")
    rel = m.group(1)
    tar_url = PMEL_BASE + rel
    print(f"  [pmel/{basin}] tarball ready: {tar_url}")

    # 4. Download.
    dest = INPUT_DIR / cfg["out_name"]
    with urllib.request.urlopen(tar_url, timeout=timeout) as r:
        data = r.read()
    dest.write_bytes(data)
    print(f"  [pmel/{basin}] saved {dest}  ({len(data)/1024:.0f} KB)")
    return dest


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--basin", choices=list(BASIN_FORMS),
                    default="pacific")
    ap.add_argument("--start", type=_parse_date,
                    help="Start date YYYY-MM-DD (default: Apr 1 of current year)")
    ap.add_argument("--end", type=_parse_date,
                    help="End date YYYY-MM-DD (default: Dec 31 of current year)")
    ap.add_argument("--timeout", type=int, default=300,
                    help="HTTP timeout per request, seconds")
    args = ap.parse_args()
    try:
        fetch_basin(args.basin, args.start, args.end, args.timeout)
    except Exception as e:
        print(f"  [pmel/{args.basin}] FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
