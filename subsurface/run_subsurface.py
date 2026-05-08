"""TAO/TRITON Pacific + RAMA Indian Ocean subsurface daily section pipeline.

Refactored from a Pacific-only Colab port to support both ocean basins via
``--basin pacific|indian|both`` (default: both). Reads tarballs from
``Input/``, writes per-day PNGs, CSV, and zip to
``Output/Subsurface/<Pacific|Indian>/``.

Inputs (per basin, see basin_config.py):
  Pacific: Input/data.tar + Input/data_p_clim_processed.tar.gz
  Indian:  Input/data_rama.tar + Input/data_rama_clim_processed.tar.gz

Indian basin additionally produces IOD-relevant box aggregation
(WTIO / SETIO + DMI proxy) into ind_iod_boxes.csv + ind_iod_timeseries.png.
"""

import argparse
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

# basin_config sits next to this script
sys.path.insert(0, str(Path(__file__).resolve().parent))
from basin_config import BASINS, BasinConfig  # noqa: E402

warnings.filterwarnings("ignore")


# ────────────────────────────────────────────────────────────
# 0. PATH LAYOUT
# ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR   = SCRIPT_DIR.parent
INPUT_DIR  = ROOT_DIR / "Input"
SUBSURFACE_OUT = ROOT_DIR / "Output" / "Subsurface"
WORK_DIR_BASE  = SCRIPT_DIR / "pmel_data"


# ────────────────────────────────────────────────────────────
# 1. CONSTANTS
# ────────────────────────────────────────────────────────────
DAYS_BACK = 15

ANOM_LEVELS = np.array([-5, -4, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, 4, 5])
ANOM_CMAP   = "RdBu_r"
ABS_LEVELS  = np.array([14, 16, 18, 20, 22, 24, 26, 28, 30])
ABS_CMAP    = "turbo"
ABS_UNDER_COLOR = "#0a1a3d"
ABS_OVER_COLOR  = "#7a0000"   # deep red for T > 30°C (warmer than turbo top)


# ────────────────────────────────────────────────────────────
# 2. HELPERS
# ────────────────────────────────────────────────────────────
def _clear_dir(d: Path) -> None:
    """Remove all contents of a directory; preserve .gitkeep."""
    if not d.is_dir():
        return
    for entry in d.iterdir():
        if entry.name == ".gitkeep":
            continue
        if entry.is_file() or entry.is_symlink():
            entry.unlink()
        else:
            shutil.rmtree(entry, ignore_errors=True)


def extract_tar(tar_path: Path, target: Path) -> None:
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


def _parse_decimal(int_part: str, dec_part: str) -> float:
    """RAMA filenames encode 80.5°E as either '80_5e' or '80.5e' depending on
    source. Handle both by joining with '.' if a decimal-part group matched.
    """
    if dec_part:
        return float(f"{int_part}.{dec_part}")
    return float(int_part)


# ────────────────────────────────────────────────────────────
# 3. FILENAME PARSERS (decimal-aware for RAMA)
# ────────────────────────────────────────────────────────────
# Raw daily file: t<lat><n|s><lon><e|w>_dy.ascii where lat/lon may have
# a decimal part separated by '.' or '_' (e.g. t0n80_5e_dy.ascii or
# t0n80.5e_dy.ascii). Lat or lon decimals are independent.
RAW_FNAME_RE = re.compile(
    r"t(\d+)(?:[._](\d+))?([ns])(\d+)(?:[._](\d+))?([ew])_dy\.ascii",
    re.I,
)
CLIM_FNAME_RE = re.compile(
    r"t(\d+)(?:[._](\d+))?([ew])_clim\.ascii",
    re.I,
)


def parse_raw_filename(path: Path):
    m = RAW_FNAME_RE.search(path.name)
    if not m:
        return None
    lat = _parse_decimal(m.group(1), m.group(2))
    if m.group(3).lower() == "s":
        lat = -lat
    lon = _parse_decimal(m.group(4), m.group(5))
    if m.group(6).lower() == "w":
        lon = -lon
    lon360 = lon if lon >= 0 else lon + 360
    return lat, lon360


def parse_clim_filename(path: Path):
    m = CLIM_FNAME_RE.search(path.name)
    if not m:
        return None
    lon = _parse_decimal(m.group(1), m.group(2))
    if m.group(3).lower() == "w":
        lon = -lon
    return lon if lon >= 0 else lon + 360


# ────────────────────────────────────────────────────────────
# 4. ASCII PARSERS
# ────────────────────────────────────────────────────────────
def parse_pmel_ascii(path: Path, max_quality: int = 3) -> pd.DataFrame:
    coords = parse_raw_filename(path)
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
# 5. BAND WEIGHTING
# ────────────────────────────────────────────────────────────
def select_lat_weights(used_lats: list[float], cfg: BasinConfig) -> dict:
    """Pick the appropriate weight scheme based on which lats are present.

    If all keys of ``lat_weights_full`` (5-point) appear, use it. Otherwise
    fall back to ``lat_weights_sparse`` and renormalize over actually-present
    lats. Lats not in either scheme are dropped.
    """
    full_keys = set(cfg.lat_weights_full)
    if full_keys.issubset(set(used_lats)):
        return dict(cfg.lat_weights_full)
    sparse = {k: v for k, v in cfg.lat_weights_sparse.items() if k in used_lats}
    if not sparse:
        # Last-resort: equal-weight whatever lats are available within the band
        if used_lats:
            return {lat: 1.0 / len(used_lats) for lat in used_lats}
        return {}
    total = sum(sparse.values())
    return {k: v / total for k, v in sparse.items()}


def make_band(raw: pd.DataFrame, cfg: BasinConfig):
    if raw.empty:
        return pd.DataFrame(), [], None
    lo, hi = cfg.lat_band
    sub = raw[(raw["latitude"] >= lo) & (raw["latitude"] <= hi)].copy()
    if sub.empty:
        print(f"   no buoys in [{lo}, {hi}]")
        return pd.DataFrame(), [], None
    used_lats = sorted(sub["latitude"].unique())
    weight_map = select_lat_weights(used_lats, cfg)
    if not weight_map:
        print(f"   no usable lats in [{lo}, {hi}]")
        return pd.DataFrame(), [], None

    # Drop rows whose lat isn't in the chosen weight scheme
    sub = sub[sub["latitude"].isin(weight_map.keys())].copy()
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

    weight_str = ", ".join(
        f"{lat:+g}°={w:.3f}" for lat, w in weight_map.items())
    print(f"                  lats used: {sorted(weight_map.keys())}")
    print(f"                  weights: {weight_str}")
    print(f"                  {sub['lon_key'].nunique()} lons, {len(g):,} cells")
    return g, list(weight_map.keys()), list(weight_map.values())


# ────────────────────────────────────────────────────────────
# 6. ANOMALY ATTACHMENT (fallback chain)
# ────────────────────────────────────────────────────────────
def attach_anomaly(obs: pd.DataFrame, clim_df: pd.DataFrame, use_clim: bool):
    if obs.empty:
        return obs, "empty"
    obs = obs.copy()

    if not use_clim or clim_df.empty:
        fb = (obs.groupby(["lon_key", "depth"], as_index=False)["T"]
                  .mean().rename(columns={"T": "T_clim"}))
        obs = obs.merge(fb, on=["lon_key", "depth"], how="left")
        obs["anom"] = obs["T"] - obs["T_clim"]
        return obs, "in-period mean (no climatology available)"

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
    return obs, label


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


def assemble_daily(obs, lon_grid, depth_grid, dates,
                   max_extrap_lon, max_extrap_dep):
    out = []
    last_T, last_A = None, None
    for d in dates:
        sub = obs[obs["date"] == pd.Timestamp(d)] if not obs.empty else obs
        T_g, A_g = make_section(sub, lon_grid, depth_grid,
                                max_extrap_lon, max_extrap_dep)
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
def actual_buoy_lons(obs):
    if obs is None or obs.empty:
        return []
    return sorted(obs["lon_key"].unique())


def lat_label_from_list(lats):
    lats = sorted(lats)
    if not lats:
        return ""
    if len(lats) == 1:
        return f"{lats[0]:+g}°"
    return f"{lats[0]:+g}° to {lats[-1]:+g}° ({len(lats)} lines)"


def plot_single(date, A, T, tag, kind, cfg: BasinConfig,
                buoy_lons, lat_label_str, savepath, baseline=""):
    lon_grid   = cfg.lon_grid
    depth_grid = cfg.depth_grid

    fig, ax = plt.subplots(figsize=(11, 4.2))

    if kind == "anomaly":
        cmap   = mpl.colormaps.get_cmap(ANOM_CMAP).copy()
        levels = ANOM_LEVELS
        cb_label = "Temperature anomaly (°C)"; cb_ticks = ANOM_LEVELS
        extend = "both"
    else:
        cmap   = mpl.colormaps.get_cmap(ABS_CMAP).copy()
        cmap.set_under(ABS_UNDER_COLOR)
        cmap.set_over(ABS_OVER_COLOR)
        levels = ABS_LEVELS
        cb_label = "Temperature (°C)"; cb_ticks = ABS_LEVELS
        extend = "both"
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
    ax.xaxis.set_major_formatter(plt.FuncFormatter(cfg.fmt_lon))
    ax.tick_params(axis="both", labelsize=8)
    ax.grid(alpha=0.25, linewidth=0.25)
    for sp in ax.spines.values():
        sp.set_linewidth(0.5)

    for lon in buoy_lons:
        ax.plot(lon, 348, marker="^", color="black", markersize=5, clip_on=False)

    if kind == "anomaly":
        for nm, (lo, hi) in cfg.overlay_boxes.items():
            ax.fill_between([lo, hi], 0, 7, color="steelblue", alpha=0.55,
                            edgecolor="none", zorder=5)
            ax.text((lo + hi) / 2, 3.5, nm, fontsize=6.5,
                    ha="center", va="center", color="white",
                    fontweight="bold", zorder=6)

    title_kind = "Anomaly" if kind == "anomaly" else "Absolute Temperature"
    title = (f"{cfg.title_label}  —  "
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
            cb.ax.text(0.5, 1.04, ">30 °C", transform=cb.ax.transAxes,
                       ha="center", va="bottom", fontsize=7, color=ABS_OVER_COLOR,
                       fontweight="bold")

    fig.tight_layout()
    fig.savefig(savepath, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return savepath


# ────────────────────────────────────────────────────────────
# 9. DIAGNOSTICS
# ────────────────────────────────────────────────────────────
def print_pacific_diagnostics(sections, cfg: BasinConfig):
    lon_grid = cfg.lon_grid
    depth_grid = cfg.depth_grid
    print("\nSubsurface (50–200 m) anomaly maximum per day, ±5° band:")
    print("─" * 60)
    for date, A, T, tag in sections:
        if np.isnan(A).all():
            print(f"   {date}   no data"); continue
        mask_d = (depth_grid >= 50) & (depth_grid <= 200)
        sub = A[mask_d, :]
        if np.isnan(sub).all():
            print(f"   {date}   all-NaN subsurface"); continue
        j = np.unravel_index(np.nanargmax(sub), sub.shape)
        print(f"   {date}   max +{sub[j]:4.2f} °C   "
              f"@ {cfg.fmt_lon(lon_grid[j[1]]):>7}"
              f"  / {int(depth_grid[mask_d][j[0]]):>3} m  {tag}")

    print("\nSurface (0–30 m) max & 28 °C span:")
    print("─" * 60)
    for date, A, T, tag in sections:
        if np.isnan(T).all():
            print(f"   {date}   no data"); continue
        sst_row = T[0, :]
        above28 = ~np.isnan(sst_row) & (sst_row > 28)
        if above28.any():
            idxs = np.where(above28)[0]
            span = (f"28°C: {cfg.fmt_lon(lon_grid[idxs[0]])} → "
                    f"{cfg.fmt_lon(lon_grid[idxs[-1]])}")
        else:
            span = "no >28°C surface"
        print(f"   {date}   SST max {np.nanmax(T[0:6, :]):4.1f} °C   {span}  {tag}")


def print_indian_diagnostics(sections, cfg: BasinConfig, iod_df: pd.DataFrame):
    lon_grid = cfg.lon_grid
    depth_grid = cfg.depth_grid

    print("\nIOD proxy (WTIO − SETIO box-mean SSTA, surface 0–10 m):")
    print("─" * 60)
    if iod_df.empty:
        print("   no IOD aggregation available")
    else:
        wide = iod_df.pivot_table(index="date", columns="box",
                                  values="ssta_mean").sort_index()
        for date, row in wide.iterrows():
            wtio = row.get("WTIO", np.nan)
            setio = row.get("SETIO", np.nan)
            dmi = wtio - setio if not (np.isnan(wtio) or np.isnan(setio)) else np.nan
            tag = ""
            if not np.isnan(dmi):
                if dmi >= 0.4:
                    tag = "(+IOD threshold)"
                elif dmi <= -0.4:
                    tag = "(−IOD threshold)"
            print(f"   {date.date()}   WTIO {wtio:+.2f}   "
                  f"SETIO {setio:+.2f}   DMI {dmi:+.2f}  {tag}")

    print("\nEquatorial thermocline tilt (20°C isotherm depth, west−east):")
    print("─" * 60)
    for date, A, T, tag in sections:
        if np.isnan(T).all():
            print(f"   {date}   no data"); continue
        # Find 20°C depth at 60°E and 90°E (nearest grid lon)
        west_idx = np.argmin(np.abs(lon_grid - 60))
        east_idx = np.argmin(np.abs(lon_grid - 90))

        def iso20_depth(col):
            if np.isnan(col).all():
                return np.nan
            # First depth where T crosses 20°C from above
            above = col > 20
            below = col <= 20
            for i in range(len(col) - 1):
                if above[i] and below[i + 1]:
                    return depth_grid[i + 1]
            return np.nan

        d_west = iso20_depth(T[:, west_idx])
        d_east = iso20_depth(T[:, east_idx])
        if np.isnan(d_west) or np.isnan(d_east):
            print(f"   {date}   20°C not found at 60°E or 90°E   {tag}")
            continue
        diff = d_west - d_east
        flag = "(+IOD-like)" if diff < 0 else ""
        print(f"   {date}   20°C depth: 60°E={int(d_west):3d}m   "
              f"90°E={int(d_east):3d}m   tilt(W−E)={int(diff):+3d}m  "
              f"{flag}  {tag}")


# ────────────────────────────────────────────────────────────
# 10. IOD BOX AGGREGATION (Indian only)
# ────────────────────────────────────────────────────────────
def aggregate_iod_boxes(raw: pd.DataFrame, clim_df: pd.DataFrame,
                        cfg: BasinConfig, dates):
    """Per-date box-mean SST anomaly for WTIO and SETIO boxes.

    Uses surface (0–10 m) buoy observations within each box. Anomaly is from
    per-lon DOY climatology (or in-period mean fallback). Returns a long-form
    DataFrame: [date, box, n_buoys, ssta_mean, ssta_std, lats_used, lons_used].
    """
    rows = []
    if raw.empty:
        return pd.DataFrame(columns=["date", "box", "n_buoys", "ssta_mean",
                                      "ssta_std", "lats_used", "lons_used"])

    # Build a (lon, doy, depth) → T_clim lookup once
    clim_lookup = (clim_df.set_index(["lon360", "doy", "depth"])["T_clim"]
                   if not clim_df.empty else pd.Series(dtype=float))

    surface = raw[(raw["depth"] <= 10.0) & (raw["depth"] >= 0.0)].copy()
    if surface.empty:
        return pd.DataFrame(columns=["date", "box", "n_buoys", "ssta_mean",
                                      "ssta_std", "lats_used", "lons_used"])

    for box_name, (lo_w, lo_e, la_s, la_n) in cfg.iod_boxes.items():
        in_box = surface[
            (surface["longitude"] >= lo_w) & (surface["longitude"] <= lo_e) &
            (surface["latitude"]  >= la_s) & (surface["latitude"]  <= la_n)
        ].copy()
        if in_box.empty:
            continue

        # Attach climatology + anomaly. If no climatology was loaded
        # (clim_lookup is an empty Series without a MultiIndex), fall back
        # to in-period mean per (lon, depth) at this surface band.
        have_clim_index = (
            len(clim_lookup) > 0
            and getattr(clim_lookup.index, "nlevels", 1) >= 3
        )
        if not have_clim_index and not in_box.empty:
            inperiod = (in_box.groupby(["lon_key", "depth"])["T"]
                        .mean().to_dict())
        else:
            inperiod = {}

        anoms = []
        for _, r in in_box.iterrows():
            lon, doy, dep, T = r["lon_key"], int(r["doy"]), float(r["depth"]), float(r["T"])
            tc = np.nan
            if have_clim_index:
                try:
                    tc = float(clim_lookup.loc[(lon, doy, dep)])
                except (KeyError, TypeError):
                    pass
            else:
                tc = inperiod.get((lon, dep), np.nan)
            anoms.append(T - tc if not np.isnan(tc) else np.nan)
        in_box["anom"] = anoms

        # Daily aggregation (require ≥2 buoys per day for robustness)
        for date in dates:
            day = in_box[in_box["date"] == pd.Timestamp(date)]
            day = day.dropna(subset=["anom"])
            n_buoys = day[["latitude", "longitude"]].drop_duplicates().shape[0]
            if n_buoys < 2:
                continue
            rows.append({
                "date": pd.Timestamp(date),
                "box": box_name,
                "n_buoys": n_buoys,
                "ssta_mean": float(day["anom"].mean()),
                "ssta_std":  float(day["anom"].std()) if len(day) > 1 else 0.0,
                "lats_used": ",".join(f"{x:g}" for x in sorted(day["latitude"].unique())),
                "lons_used": ",".join(f"{x:g}" for x in sorted(day["lon_key"].unique())),
            })

    return pd.DataFrame(rows)


def plot_iod_timeseries(iod_df: pd.DataFrame, savepath: Path):
    if iod_df.empty:
        return
    wide = iod_df.pivot_table(index="date", columns="box",
                              values="ssta_mean").sort_index()
    wide["DMI"] = wide.get("WTIO") - wide.get("SETIO")

    fig, ax = plt.subplots(figsize=(10, 5))
    if "WTIO" in wide.columns:
        ax.plot(wide.index, wide["WTIO"], "-o", label="WTIO", color="#1f77b4", lw=1.6, ms=5)
    if "SETIO" in wide.columns:
        ax.plot(wide.index, wide["SETIO"], "-o", label="SETIO", color="#d62728", lw=1.6, ms=5)
    if "DMI" in wide.columns:
        ax.plot(wide.index, wide["DMI"], "-s", label="DMI (WTIO − SETIO)",
                color="black", lw=1.8, ms=6)

    for y in (-1.0, -0.4, 0.4, 1.0):
        ax.axhline(y, color="gray", lw=0.4,
                   ls="--" if abs(y) == 0.4 else ":", alpha=0.5)
    ax.axhline(0, color="black", lw=0.5)

    ax.set_xlabel("Date")
    ax.set_ylabel("SSTA (°C)")
    ax.set_title("Indian Ocean IOD-relevant box anomalies (RAMA buoy proxy)\n"
                 "±0.4 / ±1.0 °C dashed: rough +IOD / strong-IOD reference",
                 fontsize=11, fontweight="bold")
    ax.grid(alpha=0.3, linewidth=0.4)
    ax.legend(loc="best", fontsize=9)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(savepath, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ────────────────────────────────────────────────────────────
# 11. PER-BASIN PIPELINE
# ────────────────────────────────────────────────────────────
def run_basin(cfg: BasinConfig) -> int:
    """Run the full pipeline for one basin. Returns 0 on success, 2 if the
    basin is skipped due to missing inputs (still considered non-fatal)."""
    print("\n" + "=" * 70)
    print(f"  BASIN: {cfg.name.upper()}  (output → Output/Subsurface/{cfg.output_dirname}/)")
    print("=" * 70)

    out_dir  = SUBSURFACE_OUT / cfg.output_dirname
    work_now = WORK_DIR_BASE / f"{cfg.work_subdir}_now"
    work_clim = WORK_DIR_BASE / f"{cfg.work_subdir}_clim"
    raw_tar  = INPUT_DIR / cfg.raw_tar
    clim_tar = INPUT_DIR / cfg.clim_tar

    # Output dir setup with overwrite (preserve .gitkeep)
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_dir(out_dir)

    # Auto-fetch latest raw tarball from PMEL DDS. Network failure is
    # non-fatal: we fall through and try to use whatever Input/<raw_tar>
    # is already on disk.
    print(f"=== [0] PMEL DDS auto-fetch ({cfg.raw_tar}) ===")
    try:
        import fetch_pmel
        fetch_pmel.fetch_basin(cfg.name)
    except Exception as e:
        if raw_tar.exists():
            print(f"   ! PMEL fetch failed ({e}); using existing {raw_tar.name}",
                  file=sys.stderr)
        else:
            print(f"   ! PMEL fetch failed ({e}); no local fallback",
                  file=sys.stderr)

    if not raw_tar.exists():
        print(f"   [skip] {cfg.raw_tar} not found in Input/", file=sys.stderr)
        return 2

    # Wipe and re-extract
    if work_now.is_dir():
        shutil.rmtree(work_now, ignore_errors=True)
    if work_clim.is_dir():
        shutil.rmtree(work_clim, ignore_errors=True)
    work_now.mkdir(parents=True, exist_ok=True)
    work_clim.mkdir(parents=True, exist_ok=True)

    print("=== [1] Extract input tarballs ===")
    extract_tar(raw_tar, work_now)
    has_clim = clim_tar.exists()
    if has_clim:
        extract_tar(clim_tar, work_clim)
    else:
        print(f"   [warn] {cfg.clim_tar} not found — anomaly will use in-period mean",
              file=sys.stderr)
    gunzip_dir(work_now)
    if has_clim:
        gunzip_dir(work_clim)

    now_files  = sorted(work_now.glob("*_dy.ascii"))
    clim_files = sorted(work_clim.glob("*_clim.ascii")) if has_clim else []
    print(f"   raw:  {len(now_files):3d} files")
    print(f"   clim: {len(clim_files):3d} files")

    # Filter t-profile only (exclude surface-only sst*)
    t_profile_files = [p for p in now_files if re.match(r"^t\d", p.name)]
    if not t_profile_files:
        sample = ", ".join(p.name for p in now_files[:3]) if now_files else "(none)"
        print(
            f"   [skip] {cfg.raw_tar} has no subsurface T-profile files "
            f"(`t<lat>...<lon>..._dy.ascii`).\n"
            f"          Found instead: {sample} ...\n"
            f"          Need RAMA/TAO/TRITON multi-depth profile data, not "
            f"surface-only SST (`sst*_dy.ascii`).",
            file=sys.stderr,
        )
        return 2

    print(f"\n=== [2] Parse {len(t_profile_files)} raw daily files ===")
    frames = [df for df in (parse_pmel_ascii(p) for p in t_profile_files) if not df.empty]
    if not frames:
        print("   [skip] no parseable raw data found.", file=sys.stderr)
        return 2
    raw = pd.concat(frames, ignore_index=True)
    print(f"   → {len(raw):,} rows, {raw['date'].nunique()} dates, "
          f"{raw['lon_key'].nunique()} lons, "
          f"{raw['latitude'].nunique()} lats")
    print(f"   Lons: {sorted(raw['lon_key'].unique())}")
    print(f"   Lats: {sorted(raw['latitude'].unique())}")

    clim_df = pd.DataFrame()
    if has_clim and clim_files:
        print("\n=== [3] Load per-longitude DOY climatology ===")
        cframes = [c for c in (parse_clim_ascii(p) for p in clim_files) if not c.empty]
        if cframes:
            clim_df = pd.concat(cframes, ignore_index=True)
        print(f"   → {len(clim_df):,} climatology cells across "
              f"{clim_df['lon360'].nunique() if not clim_df.empty else 0} longitudes")

    end_date   = raw["date"].max().date()
    start_date = end_date - timedelta(days=DAYS_BACK - 1)
    dates      = [start_date + timedelta(days=i) for i in range(DAYS_BACK)]
    print(f"\n=== [4] Window: {start_date} → {end_date}  ({DAYS_BACK} days) ===")

    print("\n=== [5] Meridional weighted average ===")
    band_df, used_lats, _w = make_band(raw, cfg)

    print("\n=== [6] Anomaly attachment ===")
    band_df, baseline = attach_anomaly(band_df, clim_df, use_clim=True)
    print(f"   {baseline}")
    # Also attach anomaly to the raw per-buoy frame so the CSV can carry
    # every buoy/lat (not just the ±5° band-averaged rows the plots use).
    raw_with_anom, _ = attach_anomaly(raw, clim_df, use_clim=True)

    print("\n=== [7] Section interpolation ===")
    sections = assemble_daily(
        band_df, cfg.lon_grid, cfg.depth_grid, dates,
        cfg.extrap_lon_mask, cfg.extrap_depth_mask,
    )
    if sections:
        _, _, T, _ = sections[-1]
        c = "empty" if np.isnan(T).all() else f"{100 * (1 - np.isnan(T).mean()):.0f}%"
        print(f"   latest-day coverage: {c}")

    print("\n=== [8] Generate per-day PNGs ===")
    generated = []
    if all(np.isnan(T).all() for _, _, T, _ in sections):
        print("   ⚠  all sections empty, skipping plot step")
    else:
        lons  = actual_buoy_lons(band_df)
        latL  = lat_label_from_list(used_lats)
        for date, A, T, tag in sections:
            date_str = date.strftime("%Y%m%d")
            for kind in ("anomaly", "absolute"):
                savepath = out_dir / f"{cfg.prefix}_{kind}_{date_str}.png"
                plot_single(date, A, T, tag, kind=kind, cfg=cfg,
                            buoy_lons=lons, lat_label_str=latL,
                            savepath=savepath, baseline=baseline)
                generated.append(str(savepath))
                print(f"   ✓  {savepath.name}")

    iod_df = pd.DataFrame()
    iod_csv = None
    iod_png = None
    if cfg.iod_aggregation:
        print("\n=== [10] IOD box aggregation (WTIO / SETIO) ===")
        iod_df = aggregate_iod_boxes(raw, clim_df, cfg, dates)
        if not iod_df.empty:
            # CSV goes to Output/ root (per user request); PNG stays with the
            # rest of the basin's PNGs in Output/Subsurface/Indian/.
            iod_csv = SUBSURFACE_OUT.parent / f"{cfg.name[:3]}_iod_boxes.csv"
            iod_df.to_csv(iod_csv, index=False, float_format="%.3f")
            print(f"   CSV: {iod_csv.name}  ({len(iod_df)} rows)")
            iod_png = out_dir / f"{cfg.name[:3]}_iod_timeseries.png"
            plot_iod_timeseries(iod_df, iod_png)
            print(f"   PNG: {iod_png.name}")
        else:
            print("   [warn] no IOD box rows produced (insufficient buoys per day)")

    if cfg.name == "pacific":
        print_pacific_diagnostics(sections, cfg)
    elif cfg.name == "indian":
        print_indian_diagnostics(sections, cfg, iod_df)

    print("\n=== [9] Write CSV + zip ===")
    write_outputs(raw_with_anom, generated, out_dir, cfg.prefix, cfg,
                  extra_files=[p for p in (iod_csv, iod_png) if p])
    print(f"\n✓ {cfg.name}: outputs in {out_dir}")
    return 0


# ────────────────────────────────────────────────────────────
# 12. CSV + ZIP WRITERS
# ────────────────────────────────────────────────────────────
def lon_human(x):
    if x > 180:
        return f"{int(round(360 - x))}W"
    if x == 180:
        return "180"
    return f"{int(round(x))}E"


def write_outputs(raw_with_anom, generated_pngs, out_dir: Path, prefix: str,
                  cfg, extra_files=None):
    """Write per-buoy CSV (most-recent-date snapshot) + zip.

    The plots remain a 15-day band-averaged section. The CSV carries
    every buoy / lat / lon / depth record but **only for the most recent
    date** — single-day snapshot. Daily history lives in the PNGs.
    """
    # CSV goes to Output/ root (per user request); PNGs/zip stay in out_dir.
    csv_path = out_dir.parent.parent / f"{prefix}_data.csv"
    df_out = raw_with_anom[[
        "date", "latitude", "lon_key", "depth", "doy",
        "T", "T_clim", "anom", "quality"
    ]].copy()
    df_out = df_out.rename(columns={
        "lon_key": "longitude",
        "T":       "T_observed",
        "T_clim":  "T_climatology",
        "anom":    "anomaly",
    })
    df_out["lon_label"] = df_out["longitude"].apply(lon_human)
    df_out = df_out[["date", "latitude", "longitude", "lon_label", "depth", "doy",
                     "T_observed", "T_climatology", "anomaly", "quality"]]

    # Keep only the most recent date — every buoy, every depth, single-day
    # snapshot. The 15-day history lives in the PNG sections.
    most_recent = df_out["date"].max()
    n_dropped = (df_out["date"] != most_recent).sum()
    df_out = df_out[df_out["date"] == most_recent]

    df_out = df_out.sort_values(
        ["latitude", "longitude", "depth"]).reset_index(drop=True)
    df_out.to_csv(csv_path, index=False, float_format="%.3f")
    print(f"   CSV: {csv_path.name}  "
          f"({csv_path.stat().st_size / 1024:.0f} KB, {len(df_out):,} rows, "
          f"date={most_recent.date()}, "
          f"{df_out['latitude'].nunique()} lats × {df_out['longitude'].nunique()} lons; "
          f"dropped {n_dropped} earlier-date rows)")

    zip_path = out_dir / f"{prefix}_plots.zip"
    print(f"   ZIP: {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w",
                         compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in generated_pngs:
            zf.write(f, arcname=Path(f).name)
        zf.write(csv_path, arcname=csv_path.name)
        for ef in (extra_files or []):
            if ef and Path(ef).exists():
                zf.write(ef, arcname=Path(ef).name)


# ────────────────────────────────────────────────────────────
# 13. MAIN
# ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--basin", choices=["pacific", "indian", "both"], default="both",
        help="which basin(s) to process (default: both)",
    )
    args = parser.parse_args()

    if args.basin == "both":
        targets = ["pacific", "indian"]
    else:
        targets = [args.basin]

    SUBSURFACE_OUT.mkdir(parents=True, exist_ok=True)
    WORK_DIR_BASE.mkdir(parents=True, exist_ok=True)

    n_skip = 0
    for name in targets:
        rc = run_basin(BASINS[name])
        if rc == 2:
            n_skip += 1

    if n_skip == len(targets):
        print("\nERROR: all requested basins skipped.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
