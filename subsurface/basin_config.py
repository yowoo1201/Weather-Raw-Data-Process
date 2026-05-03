"""Basin-specific configuration for the subsurface pipeline.

Pacific (TAO/TRITON) and Indian Ocean (RAMA) share the same processing flow
but differ in: input filenames, longitude grid, lat-band weights, plot
overlays, far-extrapolation thresholds, and post-processing (IOD boxes).
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np


@dataclass
class BasinConfig:
    name: str                            # "pacific" | "indian"
    output_dirname: str                  # Output/Subsurface/<this>
    work_subdir: str                     # subsurface/pmel_data/<this>_now/clim
    prefix: str                          # PNG/CSV filename prefix (e.g. "pac_5")
    raw_tar: str                         # filename in Input/
    clim_tar: str                        # filename in Input/
    lon_grid: np.ndarray
    depth_grid: np.ndarray
    lat_band: tuple                      # (lat_min, lat_max)
    lat_weights_full: dict               # 5-point weights {lat: w} for ±5°/±2°/0°
    lat_weights_sparse: dict             # 3-point weights {lat: w} for ±5°/0° only
    extrap_lon_mask: float               # max lon distance for griddata mask
    extrap_depth_mask: float             # max depth distance for griddata mask
    overlay_boxes: dict                  # {label: (lon_min, lon_max)} for anomaly plot
    title_label: str                     # Title basin descriptor
    iod_aggregation: bool = False        # run IOD box step for Indian only
    iod_boxes: dict = field(default_factory=dict)
    fmt_lon: Optional[Callable] = None   # x-axis tick formatter (basin-specific)


# ────────────────────────────────────────────────────────────
# Pacific: 130°E → 90°W, ±5° trapezoidal 5-point weights
# ────────────────────────────────────────────────────────────
def _fmt_lon_pacific(x, pos=None):
    if x > 180:
        return f"{int(360 - x)}°W"
    if x == 180:
        return "180°"
    return f"{int(x)}°E"


PACIFIC = BasinConfig(
    name="pacific",
    output_dirname="Pacific",
    work_subdir="pacific",
    prefix="pac_5",
    raw_tar="data.tar",
    clim_tar="data_p_clim_processed.tar.gz",
    lon_grid=np.arange(130, 271, 2.0),    # 130°E → 90°W (lon360)
    depth_grid=np.arange(0, 351, 5.0),
    lat_band=(-5.0, 5.0),
    lat_weights_full={-5.0: 0.150, -2.0: 0.250, 0.0: 0.200,
                      2.0: 0.250, 5.0: 0.150},
    lat_weights_sparse={-5.0: 0.150, -2.0: 0.250, 0.0: 0.200,
                        2.0: 0.250, 5.0: 0.150},  # same as full for Pacific
    extrap_lon_mask=15.0,
    extrap_depth_mask=40.0,
    overlay_boxes={
        "Niño 4":   (160, 210),
        "Niño 3.4": (190, 240),
        "Niño 3":   (210, 270),
        "Niño 1+2": (270, 280),
    },
    title_label="Equatorial Pacific (TAO/TRITON) ±5°",
    iod_aggregation=False,
    fmt_lon=_fmt_lon_pacific,
)


# ────────────────────────────────────────────────────────────
# Indian: 40°E → 100°E (all eastern), 3-point lat weights for sparse RAMA
# ────────────────────────────────────────────────────────────
def _fmt_lon_indian(x, pos=None):
    return f"{x:g}°E"


INDIAN = BasinConfig(
    name="indian",
    output_dirname="Indian",
    work_subdir="indian",
    prefix="ind_5",
    raw_tar="data_rama.tar",
    clim_tar="data_rama_clim_processed.tar.gz",
    lon_grid=np.arange(40, 101, 2.0),     # 40°E → 100°E
    depth_grid=np.arange(0, 351, 5.0),
    lat_band=(-5.0, 5.0),
    # Full 5-point weights (Pacific-style) used when ±2° buoys are present.
    lat_weights_full={-5.0: 0.150, -2.0: 0.250, 0.0: 0.200,
                      2.0: 0.250, 5.0: 0.150},
    # 3-point fallback when only ±5° + 0° are available (RAMA typical).
    lat_weights_sparse={-5.0: 0.250, 0.0: 0.500, 5.0: 0.250},
    extrap_lon_mask=20.0,                 # RAMA sparser → wider mask
    extrap_depth_mask=40.0,
    overlay_boxes={
        "WTIO":    (50, 70),
        "Central": (70, 90),
        "SETIO":   (90, 110),
    },
    title_label="Equatorial Indian (RAMA) ±5°",
    iod_aggregation=True,
    iod_boxes={
        # Saji et al. 1999 IOD definition (lon_min, lon_max, lat_min, lat_max)
        "WTIO":  (50.0, 70.0,  -10.0, 10.0),
        "SETIO": (90.0, 110.0, -10.0,  0.0),
    },
    fmt_lon=_fmt_lon_indian,
)


BASINS = {"pacific": PACIFIC, "indian": INDIAN}
