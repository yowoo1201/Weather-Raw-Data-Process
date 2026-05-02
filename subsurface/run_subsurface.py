"""TAO/TRITON Pacific subsurface daily section pipeline (local runner).

Ported from Colab notebook ``enso_buoy_doy_climatology.ipynb`` for local
execution. Reads tarballs from ``../Input/`` (relative to this script),
writes per-day PNGs, CSV, and a zip package to ``../Output/Subsurface/``.

Inputs:
  - Input/data.tar                       (PMEL TAO/TRITON raw daily ASCII)
  - Input/data_p_clim_processed.tar.gz   (per-longitude DOY climatology)

Outputs (under Output/Subsurface/):
  - pac_5_anomaly_YYYYMMDD.png
  - pac_5_absolute_YYYYMMDD.png
  - pac_5_data.csv
  - pac_5_plots.zip
"""

import gzip
import os
import re
import shutil
import sys
import tarfile
import warnings
import zipfile
from datetime import timedelta
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MultipleLocator
from scipy.interpolate import griddata

warnings.filterwarnings("ignore")

# ────────────────────────────────────────────────────────────
# 0. PATH LAYOUT
# ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR   = SCRIPT_DIR.parent
INPUT_DIR  = ROOT_DIR / "Input"
OUTPUT_DIR = ROOT_DIR / "Output" / "Subsurface"
WORK_DIR   = SCRIPT_DIR / "pmel_data"

RAW_TAR    = INPUT_DIR / "data.tar"
CLIM_TAR   = INPUT_DIR / "data_p_clim_processed.tar.gz"

NOW_DIR    = WORK_DIR / "pacific_now"
CLIM_DIR   = WORK_DIR / "pacific_clim"

def _clear_dir(d: Path) -> None:
    """Remove all contents of a directory (leave the directory itself).

    .gitkeep is preserved so the empty output directory remains committed.
    """
    if not d.is_dir():
        return
    for entry in d.iterdir():
        if entry.name == ".gitkeep":
            continue
        if entry.is_file() or entry.is_symlink():
            entry.unlink()
        else:
            shutil.rmtree(entry, ignore_errors=True)


# Overwrite semantics: wipe any prior contents of Output/Subsurface/ so stale
# PNGs from older windows don't linger alongside the fresh run. Also clear the
# extraction work dir so we never mix stale ASCII with the new tarballs.
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_clear_dir(OUTPUT_DIR)
if WORK_DIR.is_dir():
    shutil.rmtree(WORK_DIR, ignore_errors=True)
NOW_DIR.mkdir(parents=True, exist_ok=True)
CLIM_DIR.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────
# 1. CONFIG
# ────────────────────────────────────────────────────────────
DAYS_BACK = 15

PAC_LON_GRID = np.arange(130, 271, 2.0)   # 130°E → 90°W
DEPTH_GRID   = np.arange(0, 351, 5.0)     # 0–350 m

PAC_BANDS = [
    # (name, label, lat_min, lat_max, use_clim)
    ("pac_5", "±5° (CPC standard)", -5.0, 5.0, True),
]

ANOM_LEVELS = np.array([-5, -4, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, 4, 5])
ANOM_CMAP   = "RdBu_r"
ABS_LEVELS  = np.array([14, 16, 18, 20, 22, 24, 26, 28, 30])
ABS_CMAP    = "turbo"
ABS_UNDER_COLOR = "#0a1a3d"

NINO_BOXES = {
    "Niño 4":   (160, 210),
    "Niño 3.4": (190, 240),
    "Niño 3":   (210, 270),
    "Niño 1+2": (270, 280),
}


# ────────────────────────────────────────────────────────────
# 2. EXTRACTION
# ────────────────────────────────────────────────────────────
def extract_tar(tar_path: Path, target: Path) -> None:
    if not tar_path.exists():
        raise FileNotFoundError(f"Missing input tarball: {tar_path}")
    open_mode = "r:gz" if tar_path.name.endswith((".tar.gz", ".tgz")) else "r"
    with tarfile.open(tar_path, open_mode) as tf:
        for m in tf.getmembers():
            if m.isfile() and m.name.endswith((".gz", ".ascii", ".txt")):
                m.name = os.path.basename(m.name)
                tf.extract(m, target)
    print(f"   ⏏  {tar_path.name}  →  {target}/")


def gunzip_dir(d: Path) -> None:
    for gz in d.glob("*.gz"):
        out = gz.with_suffix("")
        if not out.exists():
            with gzip.open(gz, "rb") as f_in, open(out, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)


def stage_inputs() -> tuple[list[Path], list[Path]]:
    print("=== [1] Extract input tarballs ===")
    extract_tar(RAW_TAR,  NOW_DIR)
    extract_tar(CLIM_TAR, CLIM_DIR)
    gunzip_dir(NOW_DIR)
    gunzip_dir(CLIM_DIR)
    now_files  = sorted(NOW_DIR.glob("*_dy.ascii"))
    clim_files = sorted(CLIM_DIR.glob("*_clim.ascii"))
    print(f"   pacific_now:   {len(now_files):3d} files (raw daily)")
    print(f"   pacific_clim:  {len(clim_files):3d} files (per-lon climatology)")
    return now_files, clim_files


# ────────────────────────────────────────────────────────────
# 3. PMEL DDS ASCII PARSER (raw daily)
# ────────────────────────────────────────────────────────────
FNAME_RE = re.compile(
    r"t(\d+(?:\.\d+)?)([ns])(\d+(?:\.\d+)?)([ew])_dy\.ascii",
    re.I,
)


def parse_filename(path: Path):
    m = FNAME_RE.search(path.name)
    if not m:
        return None
    lat_v = float(m.group(1)); lat_h = m.group(2).lower()
    lon_v = float(m.group(3)); lon_h = m.group(4).lower()
    lat = -lat_v if lat_h == "s" else lat_v
    lon = -lon_v if lon_h == "w" else lon_v
    lon360 = lon if lon >= 0 else lon + 360
    return lat, lon360


def parse_pmel_ascii(path: Path, max_quality: int = 3) -> pd.DataFrame:
    coords = parse_filename(path)
    if coords is None:
        return pd.DataFrame()
    lat, lon360 = coords
    with open(path) as f:
        lines = f.readlines()

    blocks = []
    cur_depths = None
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if s.startswith("Depth(M):"):
            d = []
            for tok in ln.split(":", 1)[1].split():
                try:
                    d.append(float(tok))
                except ValueError:
                    break
            cur_depths = d
        elif s.startswith("YYYYMMDD") and cur_depths is not None:
            blocks.append((cur_depths, i + 1))
            cur_depths = None
    if not blocks:
        return pd.DataFrame()

    records = []
    for b_idx, (depths, start) in enumerate(blocks):
        end = blocks[b_idx + 1][1] - 4 if b_idx + 1 < len(blocks) else len(lines)
        nd = len(depths)
        for ln in lines[start:end]:
            toks = ln.split()
            if len(toks) < 2 + nd + 2:
                continue
            try:
                ymd = toks[0]
                temps = [float(t) for t in toks[2:2 + nd]]
            except ValueError:
                continue
            qstr = toks[2 + nd]
            if len(qstr) != nd:
                continue
            try:
                time = pd.Timestamp(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}")
            except Exception:
                continue
            for d, T, q_ch in zip(depths, temps, qstr):
                if T < -9 or T > 40:
                    continue
                try:
                    q = int(q_ch)
                except ValueError:
                    continue
                if q < 1 or q > max_quality:
                    continue
                records.append({
                    "time": time, "latitude": lat, "longitude": lon360,
                    "depth": d, "T": T, "quality": q,
                })
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame.from_records(records)
    df["date"]    = df["time"].dt.normalize()
    df["lon_key"] = df["longitude"].round(2)
    df["doy"]     = df["time"].dt.dayofyear
    return df


# ────────────────────────────────────────────────────────────
# 4. CLIMATOLOGY LOADER
# ────────────────────────────────────────────────────────────
CLIM_FNAME_RE = re.compile(r"t(\d+(?:\.\d+)?)([ew])_clim\.ascii", re.I)


def parse_clim_filename(path: Path):
    m = CLIM_FNAME_RE.search(path.name)
    if not m:
        return None
    lon_v = float(m.group(1)); lon_h = m.group(2).lower()
    lon = -lon_v if lon_h == "w" else lon_v
    return lon if lon >= 0 else lon + 360


def parse_clim_ascii(path: Path) -> pd.DataFrame:
    lon360 = parse_clim_filename(path)
    if lon360 is None:
        return pd.DataFrame()
    with open(path) as f:
        lines = f.readlines()
    depth_line = next((ln for ln in lines if ln.lstrip().startswith("Depth(M):")), None)
    if depth_line is None:
        return pd.DataFrame()
    depths = [float(t) for t in depth_line.split(":", 1)[1].split()
              if t.replace(".", "").replace("-", "").isdigit()]
    nd = len(depths)
    data_start = next((i + 1 for i, ln in enumerate(lines)
                       if ln.lstrip().startswith("DOY")), None)
    if data_start is None:
        return pd.DataFrame()
    rows = []
    for ln in lines[data_start:]:
        toks = ln.split()
        if not toks:
            continue
        try:
            doy = int(toks[0])
        except ValueError:
            continue
        if len(toks) < 1 + 3 * nd:
            continue
        try:
            Ts = [float(t) for t in toks[1:1 + nd]]
            Ns = [int(t)   for t in toks[1 + nd:1 + 2 * nd]]
            Ws = [int(t)   for t in toks[1 + 2 * nd:1 + 3 * nd]]
        except ValueError:
            continue
        for d, T, N, W in zip(depths, Ts, Ns, Ws):
            if T < -9:
                continue
            rows.append({"lon360": lon360, "doy": doy, "depth": d,
                         "T_clim": T, "n_clim": N, "window_used": W})
    return pd.DataFrame(rows)


# ────────────────────────────────────────────────────────────
# 5. MERIDIONAL BAND AVERAGE
# ────────────────────────────────────────────────────────────
def compute_band_weights(lats_sorted, lat_min, lat_max):
    n = len(lats_sorted)
    if n == 0:
        return np.array([])
    if n == 1:
        return np.array([1.0])
    boundaries = [lat_min]
    for i in range(n - 1):
        boundaries.append((lats_sorted[i] + lats_sorted[i + 1]) / 2.0)
    boundaries.append(lat_max)
    widths = np.diff(boundaries)
    return widths / widths.sum()


def make_band(raw, lat_min, lat_max, name, label):
    if raw.empty:
        return pd.DataFrame(), [], None
    sub = raw[(raw["latitude"] >= lat_min) & (raw["latitude"] <= lat_max)].copy()
    if sub.empty:
        print(f"   {name:12s}  no buoys in [{lat_min}, {lat_max}]")
        return pd.DataFrame(), [], None
    used_lats = sorted(sub["latitude"].unique())
    used_lons = sorted(sub["longitude"].unique())

    weights = compute_band_weights(used_lats, lat_min, lat_max)
    weight_map = dict(zip(used_lats, weights))
    sub["_w"]   = sub["latitude"].map(weight_map).astype(float)
    sub["_T_w"] = sub["T"] * sub["_w"]

    g = sub.groupby(["date", "lon_key", "depth", "doy"], as_index=False).agg(
        T_w_sum  = ("_T_w", "sum"),
        w_sum    = ("_w",   "sum"),
        longitude= ("longitude", "mean"),
        latitude = ("latitude", "mean"),
        n_lats   = ("latitude", "nunique"),
    )
    g["T"] = g["T_w_sum"] / g["w_sum"]
    g = g.drop(columns=["T_w_sum", "w_sum"])

    weight_str = ", ".join(f"{lat:+g}°={w:.3f}" for lat, w in zip(used_lats, weights))
    print(f"   {name:12s}  {label:32s}")
    print(f"                  lats: {used_lats}")
    print(f"                  weights (trapezoidal): {weight_str}")
    print(f"                  {len(used_lons)} lons, {len(g):,} cells")
    return g, used_lats, weights


# ────────────────────────────────────────────────────────────
# 6. ANOMALY (per-lon DOY clim with fallback chain)
# ────────────────────────────────────────────────────────────
def attach_anomaly(obs, clim_df, use_clim, name):
    if obs.empty:
        return obs, "empty", 0, 0
    obs = obs.copy()

    if not use_clim or clim_df.empty:
        fb = (obs.groupby(["lon_key", "depth"], as_index=False)["T"]
                  .mean().rename(columns={"T": "T_clim"}))
        obs = obs.merge(fb, on=["lon_key", "depth"], how="left")
        obs["anom"] = obs["T"] - obs["T_clim"]
        return obs, "in-period mean (band excluded from climatology)", 0, len(obs)

    clim_keyed = clim_df.rename(columns={"lon360": "lon_key"})[
        ["lon_key", "depth", "doy", "T_clim"]]
    obs = obs.merge(clim_keyed, on=["lon_key", "depth", "doy"], how="left")
    n_exact = obs["T_clim"].notna().sum()

    if obs["T_clim"].isna().any():
        for (lk, doy), grp in clim_df.groupby(["lon360", "doy"]):
            grp_sorted = grp.sort_values("depth")
            if len(grp_sorted) < 2:
                continue
            mask = ((obs["lon_key"] == lk) & (obs["doy"] == doy) & obs["T_clim"].isna())
            if mask.any():
                obs.loc[mask, "T_clim"] = np.interp(
                    obs.loc[mask, "depth"].values,
                    grp_sorted["depth"].values,
                    grp_sorted["T_clim"].values,
                    left=grp_sorted["T_clim"].iloc[0],
                    right=grp_sorted["T_clim"].iloc[-1],
                )
    n_after_depth = obs["T_clim"].notna().sum()

    if obs["T_clim"].isna().any():
        for idx in obs[obs["T_clim"].isna()].index:
            lk  = obs.at[idx, "lon_key"]
            d   = obs.at[idx, "depth"]
            doy = obs.at[idx, "doy"]
            cands = clim_df[(clim_df["depth"] == d) & (clim_df["doy"] == doy)]
            if cands.empty:
                cands = clim_df[clim_df["doy"] == doy]
                if cands.empty:
                    continue
                nearest_lon = cands.iloc[
                    (cands["lon360"] - lk).abs().argsort()[0]
                ]["lon360"]
                grp = cands[cands["lon360"] == nearest_lon].sort_values("depth")
                if len(grp) < 2:
                    continue
                obs.at[idx, "T_clim"] = float(np.interp(
                    d, grp["depth"].values, grp["T_clim"].values,
                    left=grp["T_clim"].iloc[0], right=grp["T_clim"].iloc[-1]))
            else:
                nearest_idx = (cands["lon360"] - lk).abs().argsort().iloc[0]
                obs.at[idx, "T_clim"] = cands.iloc[nearest_idx]["T_clim"]
    n_after_lon = obs["T_clim"].notna().sum()

    n_fallback = 0
    if obs["T_clim"].isna().any():
        fb = (obs.groupby(["lon_key", "depth"], as_index=False)["T"]
                  .mean().rename(columns={"T": "_T_inperiod"}))
        obs = obs.merge(fb, on=["lon_key", "depth"], how="left")
        n_fallback = obs["T_clim"].isna().sum()
        obs.loc[obs["T_clim"].isna(), "T_clim"] = (
            obs.loc[obs["T_clim"].isna(), "_T_inperiod"])
        obs = obs.drop(columns=["_T_inperiod"])

    obs["anom"] = obs["T"] - obs["T_clim"]
    label = (f"PMEL DOY clim "
             f"(exact={n_exact}, +depth_interp={n_after_depth - n_exact}, "
             f"+lon_interp={n_after_lon - n_after_depth}, "
             f"+inperiod_fallback={n_fallback})")
    return obs, label, n_exact, len(obs)


# ────────────────────────────────────────────────────────────
# 7. SECTION INTERPOLATION
# ────────────────────────────────────────────────────────────
def make_section(df_day, lon_grid, depth_grid,
                 max_extrap_lon=15.0, max_extrap_dep=40.0):
    nan_pair = (np.full((len(depth_grid), len(lon_grid)), np.nan),
                np.full((len(depth_grid), len(lon_grid)), np.nan))
    if df_day.empty or len(df_day) < 4:
        return nan_pair
    obs_lon = df_day["longitude"].values
    obs_dep = df_day["depth"].values
    T = df_day["T"].values
    A = df_day["anom"].values
    n_lons = len(np.unique(obs_lon))
    n_deps = len(np.unique(obs_dep))

    if n_lons < 2:
        the_lon = float(np.unique(obs_lon)[0])
        ds = (pd.DataFrame({"depth": obs_dep, "T": T, "A": A})
                .groupby("depth", as_index=False).mean()
                .sort_values("depth"))
        if len(ds) < 2:
            return nan_pair
        T_1d = np.interp(depth_grid, ds["depth"], ds["T"], left=np.nan, right=np.nan)
        A_1d = np.interp(depth_grid, ds["depth"], ds["A"], left=np.nan, right=np.nan)
        T_g = np.tile(T_1d[:, None], (1, len(lon_grid)))
        A_g = np.tile(A_1d[:, None], (1, len(lon_grid)))
        far_lon = np.abs(lon_grid - the_lon) > max_extrap_lon
        T_g[:, far_lon] = np.nan; A_g[:, far_lon] = np.nan
        ddep = np.abs(depth_grid[:, None] - ds["depth"].values[None, :]).min(axis=1)
        T_g[ddep > max_extrap_dep, :] = np.nan
        A_g[ddep > max_extrap_dep, :] = np.nan
        return T_g, A_g

    if n_deps < 2:
        return nan_pair

    pts = np.column_stack([obs_lon, obs_dep])
    LO, DE = np.meshgrid(lon_grid, depth_grid)
    try:
        T_g = griddata(pts, T, (LO, DE), method="linear")
        A_g = griddata(pts, A, (LO, DE), method="linear")
    except Exception:
        return nan_pair
    dlon = np.abs(LO[..., None] - obs_lon[None, None, :]).min(axis=2)
    ddep = np.abs(DE[..., None] - obs_dep[None, None, :]).min(axis=2)
    far  = (dlon > max_extrap_lon) | (ddep > max_extrap_dep)
    if np.isnan(T_g).any():
        T_g = np.where(np.isnan(T_g), griddata(pts, T, (LO, DE), method="nearest"), T_g)
    if np.isnan(A_g).any():
        A_g = np.where(np.isnan(A_g), griddata(pts, A, (LO, DE), method="nearest"), A_g)
    T_g[far] = np.nan; A_g[far] = np.nan
    return T_g, A_g


def assemble_daily(obs, lon_grid, depth_grid, dates):
    out = []
    last_T, last_A = None, None
    for d in dates:
        sub = obs[obs["date"] == pd.Timestamp(d)] if not obs.empty else obs
        T_g, A_g = make_section(sub, lon_grid, depth_grid)
        if np.isnan(T_g).all() and last_T is not None:
            T_g, A_g = last_T.copy(), last_A.copy(); tag = "(carry)"
        elif np.isnan(T_g).all():
            tag = "(empty)"
        else:
            last_T, last_A = T_g, A_g; tag = ""
        out.append((d, A_g, T_g, tag))
    return out


# ────────────────────────────────────────────────────────────
# 8. PLOTTING
# ────────────────────────────────────────────────────────────
def fmt_lon(x, pos=None):
    if x > 180:
        return f"{int(360 - x)}°W"
    if x == 180:
        return "180°"
    return f"{int(x)}°E"


def actual_buoy_lons(obs):
    if obs is None or obs.empty:
        return []
    return sorted(obs["lon_key"].unique())


def lat_label_from_list(lats, fallback):
    if not lats:
        return fallback
    if len(lats) == 1:
        return f"{lats[0]:+g}°"
    return f"{lats[0]:+g}° to {lats[-1]:+g}° ({len(lats)} lines)"


def plot_single(date, A, T, tag, kind, lon_grid, depth_grid,
                buoy_lons, lat_label_str, savepath, baseline=""):
    fig, ax = plt.subplots(figsize=(11, 4.2))

    if kind == "anomaly":
        cmap   = mpl.colormaps.get_cmap(ANOM_CMAP).copy()
        levels = ANOM_LEVELS
        cb_label = "Temperature anomaly (°C)"; cb_ticks = ANOM_LEVELS
        extend = "both"
    else:
        cmap   = mpl.colormaps.get_cmap(ABS_CMAP).copy()
        cmap.set_under(ABS_UNDER_COLOR)
        levels = ABS_LEVELS
        cb_label = "Temperature (°C)"; cb_ticks = ABS_LEVELS
        extend = "min"
    norm = mpl.colors.BoundaryNorm(levels, cmap.N, extend=extend)

    field = A if kind == "anomaly" else T
    im = None
    if not np.isnan(field).all():
        im = ax.contourf(lon_grid, depth_grid, field, levels=levels,
                         cmap=cmap, norm=norm, extend=extend)

        if kind == "anomaly":
            ax.contour(lon_grid, depth_grid, A, levels=[-0.5, 0.5],
                       colors="black", linewidths=0.3, alpha=0.4,
                       linestyles="dashed")
            ax.contour(lon_grid, depth_grid, A, levels=np.arange(-5, 6, 1),
                       colors="black", linewidths=0.4, alpha=0.55)
            ax.contour(lon_grid, depth_grid, A, levels=[0],
                       colors="black", linewidths=0.9)

        ISO_LEVELS = [18, 20, 22, 24, 26, 28, 30]
        iso28_color = "white" if kind == "absolute" else "red"
        if kind == "absolute":
            thin_color, thin_lw, thin_alpha = "white", 1.0, 0.85
        else:
            thin_color, thin_lw, thin_alpha = "0.30", 0.5, 0.45
        for iso in ISO_LEVELS:
            if iso == 20:
                c, lw, alpha = "black", 2.0, 0.95
            elif iso == 28:
                c, lw, alpha = iso28_color, 2.0, 0.95
            else:
                c, lw, alpha = thin_color, thin_lw, thin_alpha
            cs = ax.contour(lon_grid, depth_grid, T, levels=[iso],
                            colors=c, linewidths=lw, alpha=alpha)
            if len(cs.allsegs) > 0 and len(cs.allsegs[0]) > 0:
                lbl_color = "white" if (kind == "absolute" and iso != 20) else "black"
                ax.clabel(cs, inline=True, fontsize=8, fmt=f"{iso}°",
                          inline_spacing=3, colors=lbl_color)
    else:
        ax.text(0.5, 0.5, "no data", transform=ax.transAxes,
                ha="center", va="center", fontsize=14, color="gray")

    ax.set_ylim(350, 0)
    ax.set_yticks([0, 50, 100, 150, 200, 250, 300])
    ax.set_xlim(lon_grid.min(), lon_grid.max())
    ax.set_xlabel("Longitude", fontsize=10)
    ax.set_ylabel("Depth (m)", fontsize=10)
    ax.xaxis.set_major_locator(MultipleLocator(10))
    ax.xaxis.set_major_formatter(plt.FuncFormatter(fmt_lon))
    ax.tick_params(axis="both", labelsize=8)
    ax.grid(alpha=0.25, linewidth=0.25)
    for sp in ax.spines.values():
        sp.set_linewidth(0.5)

    for lon in buoy_lons:
        ax.plot(lon, 348, marker="^", color="black", markersize=5, clip_on=False)

    if kind == "anomaly":
        for nm, (lo, hi) in NINO_BOXES.items():
            ax.fill_between([lo, hi], 0, 7, color="steelblue", alpha=0.55,
                            edgecolor="none", zorder=5)
            ax.text((lo + hi) / 2, 3.5, nm, fontsize=6.5,
                    ha="center", va="center", color="white",
                    fontweight="bold", zorder=6)

    title_kind = "Anomaly" if kind == "anomaly" else "Absolute Temperature"
    title = (f"Equatorial Pacific (TAO/TRITON) ±5°  —  "
             f"{date.strftime('%b %d, %Y')}  —  Daily {title_kind}")
    if tag:
        title += f"  {tag}"
    sub = f"lats: {lat_label_str}"
    if kind == "anomaly" and baseline:
        sub += f"    |    {baseline}"
    ax.set_title(title + "\n" + sub, fontsize=10, fontweight="bold", pad=8,
                 loc="center")

    if im is not None:
        cb = fig.colorbar(im, ax=ax, ticks=cb_ticks, extend=extend,
                          fraction=0.04, pad=0.02)
        cb.set_label(cb_label, fontsize=9)
        cb.ax.tick_params(labelsize=8)
        if kind == "absolute":
            cb.ax.text(0.5, -0.08, "≤14 °C", transform=cb.ax.transAxes,
                       ha="center", va="top", fontsize=7, color=ABS_UNDER_COLOR,
                       fontweight="bold")

    fig.tight_layout()
    fig.savefig(savepath, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return savepath


# ────────────────────────────────────────────────────────────
# 9. DIAGNOSTICS + OUTPUT
# ────────────────────────────────────────────────────────────
def print_diagnostics(sections):
    print("\nSubsurface (50–200 m) anomaly maximum per day, ±5° band:")
    print("─" * 60)
    for date, A, T, tag in sections:
        if np.isnan(A).all():
            print(f"   {date}   no data"); continue
        mask_d = (DEPTH_GRID >= 50) & (DEPTH_GRID <= 200)
        sub = A[mask_d, :]
        if np.isnan(sub).all():
            print(f"   {date}   all-NaN subsurface"); continue
        j = np.unravel_index(np.nanargmax(sub), sub.shape)
        print(f"   {date}   max +{sub[j]:4.2f} °C   "
              f"@ {fmt_lon(PAC_LON_GRID[j[1]]):>7}"
              f"  / {int(DEPTH_GRID[mask_d][j[0]]):>3} m  {tag}")

    print("\nSurface (0–30 m) max & 28 °C span:")
    print("─" * 60)
    for date, A, T, tag in sections:
        if np.isnan(T).all():
            print(f"   {date}   no data"); continue
        sst_row = T[0, :]
        above28 = ~np.isnan(sst_row) & (sst_row > 28)
        if above28.any():
            idxs = np.where(above28)[0]
            span = (f"28°C: {fmt_lon(PAC_LON_GRID[idxs[0]])} → "
                    f"{fmt_lon(PAC_LON_GRID[idxs[-1]])}")
        else:
            span = "no >28°C surface"
        print(f"   {date}   SST max {np.nanmax(T[0:6, :]):4.1f} °C   {span}  {tag}")


def lon_human(x):
    if x > 180:
        return f"{int(round(360 - x))}W"
    if x == 180:
        return "180"
    return f"{int(round(x))}E"


def write_outputs(band_df, generated_pngs, name="pac_5"):
    csv_path = OUTPUT_DIR / f"{name}_data.csv"
    df_out = band_df[[
        "date", "lon_key", "depth", "doy", "T", "T_clim", "anom", "n_lats", "latitude"
    ]].copy()
    df_out = df_out.rename(columns={
        "lon_key":  "longitude",
        "T":        "T_observed",
        "T_clim":   "T_climatology",
        "anom":     "anomaly",
        "latitude": "mean_lat_used",
    })
    df_out["lon_label"] = df_out["longitude"].apply(lon_human)
    df_out = df_out[["date", "longitude", "lon_label", "depth", "doy",
                     "T_observed", "T_climatology", "anomaly",
                     "n_lats", "mean_lat_used"]]
    df_out = df_out.sort_values(["date", "longitude", "depth"]).reset_index(drop=True)
    df_out.to_csv(csv_path, index=False, float_format="%.3f")
    print(f"\nCSV: {csv_path}  "
          f"({csv_path.stat().st_size / 1024:.0f} KB, {len(df_out):,} rows)")

    zip_path = OUTPUT_DIR / f"{name}_plots.zip"
    print(f"Packaging {len(generated_pngs)} PNGs + 1 CSV → {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w",
                         compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in generated_pngs:
            zf.write(f, arcname=Path(f).name)
        zf.write(csv_path, arcname=csv_path.name)
    total_kb = sum(Path(f).stat().st_size for f in generated_pngs + [csv_path]) / 1024
    print(f"   total uncompressed: {total_kb:6.0f} KB")
    print(f"   zip size          : {zip_path.stat().st_size / 1024:6.0f} KB")


# ────────────────────────────────────────────────────────────
# 10. MAIN
# ────────────────────────────────────────────────────────────
def main():
    now_files, clim_files = stage_inputs()
    has_clim = len(clim_files) > 0

    print("\n=== [2] Parse PMEL raw daily files ===")
    # Subsurface T-profile files start with `t<digit>`. Surface-SST files
    # (`sst<...>_dy.ascii`) also superficially match the FNAME_RE regex via
    # `.search()`, so we exclude them by prefix here.
    t_profile_files = [p for p in now_files if re.match(r"^t\d", p.name)]
    if not t_profile_files:
        sample = ", ".join(p.name for p in now_files[:3]) if now_files else "(none)"
        print(
            "ERROR: Input/data.tar contains no subsurface temperature profile "
            "files (`t<lat><n|s><lon><e|w>_dy.ascii`).",
            file=sys.stderr,
        )
        print(
            f"       Found instead: {sample} ...\n"
            f"       The pipeline requires PMEL TAO/TRITON multi-depth profile "
            f"data (e.g. `t0n140w_dy.ascii`), NOT surface-only SST data "
            f"(`sst*_dy.ascii`). Replace Input/data.tar with the correct "
            f"tarball from the PMEL DDS portal.",
            file=sys.stderr,
        )
        sys.exit(2)
    frames = [df for df in (parse_pmel_ascii(p) for p in t_profile_files) if not df.empty]
    if not frames:
        print("ERROR: no parseable raw data found.", file=sys.stderr)
        sys.exit(1)
    pac_raw = pd.concat(frames, ignore_index=True)
    print(f"   → {len(pac_raw):,} rows, {pac_raw['date'].nunique()} dates, "
          f"{pac_raw['lon_key'].nunique()} buoy lons, "
          f"{pac_raw['latitude'].nunique()} buoy lats")
    print(f"   Lons: {sorted(pac_raw['lon_key'].unique())}")
    print(f"   Lats: {sorted(pac_raw['latitude'].unique())}")

    pac_clim = pd.DataFrame()
    if has_clim:
        print("\n=== [3] Load per-longitude DOY climatology ===")
        cframes = [parse_clim_ascii(p) for p in clim_files]
        cframes = [c for c in cframes if not c.empty]
        if cframes:
            pac_clim = pd.concat(cframes, ignore_index=True)
        print(f"   → {len(pac_clim):,} climatology cells across "
              f"{pac_clim['lon360'].nunique() if not pac_clim.empty else 0} longitudes")

    end_date   = pac_raw["date"].max().date()
    start_date = end_date - timedelta(days=DAYS_BACK - 1)
    dates      = [start_date + timedelta(days=i) for i in range(DAYS_BACK)]
    print(f"\n=== [4] Window: {start_date} → {end_date}  ({DAYS_BACK} days) ===")

    print("\n=== [5] Meridional weighted average ===")
    pac_bands = {}
    pac_band_lats = {}
    for name, label, lo, hi, _uc in PAC_BANDS:
        pac_bands[name], pac_band_lats[name], _w = make_band(
            pac_raw, lo, hi, name, label)

    print("\n=== [6] Anomaly attachment ===")
    pac_baselines = {}
    band_use_clim = {n: uc for n, _l, _lo, _hi, uc in PAC_BANDS}
    for name in pac_bands:
        pac_bands[name], pac_baselines[name], _ne, _nt = attach_anomaly(
            pac_bands[name], pac_clim, band_use_clim[name], name)
        if not pac_bands[name].empty:
            print(f"   {name:12s}  {pac_baselines[name]}")

    print("\n=== [7] Section interpolation ===")
    pac_sections = {n: assemble_daily(pac_bands[n], PAC_LON_GRID, DEPTH_GRID, dates)
                    for n in pac_bands}
    for n in pac_bands:
        secs = pac_sections[n]
        if secs:
            _, _, T, _ = secs[-1]
            c = "empty" if np.isnan(T).all() else f"{100 * (1 - np.isnan(T).mean()):.0f}%"
            print(f"   {n:12s} latest-day coverage: {c}")

    print("\n=== [8] Generate per-day PNGs ===")
    generated = []
    for name, label, _lo, _hi, _uc in PAC_BANDS:
        secs = pac_sections[name]
        if all(np.isnan(T).all() for _, _, T, _ in secs):
            print(f"   ⚠  {name} skipped (no data)"); continue
        obs   = pac_bands[name]
        lons  = actual_buoy_lons(obs)
        latL  = lat_label_from_list(pac_band_lats.get(name, []), label)
        bl    = pac_baselines.get(name, "")
        for date, A, T, tag in secs:
            date_str = date.strftime("%Y%m%d")
            for kind in ("anomaly", "absolute"):
                savepath = OUTPUT_DIR / f"{name}_{kind}_{date_str}.png"
                plot_single(date, A, T, tag, kind=kind,
                            lon_grid=PAC_LON_GRID, depth_grid=DEPTH_GRID,
                            buoy_lons=lons, lat_label_str=latL,
                            savepath=savepath, baseline=bl)
                generated.append(str(savepath))
                print(f"   ✓  {savepath.name}")

    if "pac_5" in pac_sections:
        print_diagnostics(pac_sections["pac_5"])

    print("\n=== [9] Write CSV + zip ===")
    write_outputs(pac_bands["pac_5"], generated, name="pac_5")

    print(f"\n✓ Done. Outputs in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
