# Auto-extracted from ElNinoRSSTDailyCalculation.ipynb (cell 0)
# Runs the daily ENSO rSSTA pipeline locally.
#
# Layout (relative to project root Code/):
#   Input/oisst_data/oisst-avhrr-v02r01.YYYYMMDD.nc
#   Input/nino_clim_daily_1991-2020.csv
#   Input/wksst9120.for.txt
#   Input/rel_wksst9120.txt
#   Output/SST/   ← all generated CSV / PNG / report / zip

import os, re, glob
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pathlib import Path

# ==== 경로 ====
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR   = SCRIPT_DIR.parent
INPUT_DIR  = ROOT_DIR / "Input"
OUT_DIR    = ROOT_DIR / "Output" / "SST"
CSV_DIR    = ROOT_DIR / "Output"          # CSVs land here (flat, not in subfolder)

NC_DIR       = str(INPUT_DIR / "oisst_data")
CLIM_FILE    = str(INPUT_DIR / "nino_clim_daily_1991-2020.csv")
ABS_FILE     = str(INPUT_DIR / "wksst9120.for.txt")
REL_FILE     = str(INPUT_DIR / "rel_wksst9120.txt")
MTH_ABS_FILE = str(INPUT_DIR / "sstoi.indices.txt")
MTH_REL_FILE = str(INPUT_DIR / "rel_mthsst9120.txt")
RONI_K_FILE  = str(INPUT_DIR / "RONI_K_monthly.csv")
OUT_DIR      = str(OUT_DIR)
CSV_DIR      = str(CSV_DIR)

# Auto-fetch latest CPC weekly + monthly SST bulletins. CPC overwrites these
# files in place on their server, so we always pull fresh on each run; if the
# network is unreachable we fall back to whatever is already on disk.
import sys, urllib.request
INPUT_DIR.mkdir(parents=True, exist_ok=True)
_CPC_TARGETS = [
    ("https://cpc.ncep.noaa.gov/data/indices/wksst9120.for", ABS_FILE),
    ("https://cpc.ncep.noaa.gov/data/indices/rel_wksst9120.txt", REL_FILE),
    ("https://cpc.ncep.noaa.gov/data/indices/sstoi.indices", MTH_ABS_FILE),
    ("https://cpc.ncep.noaa.gov/data/indices/rel_mthsst9120.txt", MTH_REL_FILE),
]
print("[0] CPC 주간/월간 SST bulletin 자동 fetch")
for _url, _dest in _CPC_TARGETS:
    try:
        with urllib.request.urlopen(_url, timeout=20) as _r:
            _data = _r.read()
        with open(_dest, "wb") as _f:
            _f.write(_data)
        print(f"    ↓ {_url}\n      → {_dest}  ({len(_data)/1024:.0f} KB)")
    except Exception as _e:
        if os.path.exists(_dest):
            print(f"    ! fetch failed ({_e}); using existing {_dest}",
                  file=sys.stderr)
        else:
            print(f"    ! fetch failed ({_e}); no local fallback at {_dest}",
                  file=sys.stderr)
            raise

# Auto-fetch newest OISST .nc files. Walks the current and previous
# month directories on NCEI and reconciles each date's local file:
#   - missing → download (final preferred, prelim if final not yet posted)
#   - prelim only locally + final remote → download final, drop prelim
#   - both final + prelim locally → drop prelim
# Network failure here is non-fatal: we log and continue with whatever
# .nc files are already in Input/oisst_data/.
print("[0b] OISST .nc 자동 fetch (current+previous month)")
try:
    sys.path.insert(0, str(SCRIPT_DIR))
    import fetch_oisst as _fo
    _fo.fetch_months(_fo.default_months())
except Exception as _e:
    print(f"    ! OISST fetch skipped ({_e}); using existing local files",
          file=sys.stderr)

# Overwrite semantics: wipe any prior contents of Output/SST/ so stale files
# from previous runs (different date windows, different report content) don't
# linger alongside fresh output. Preserve .gitkeep so the empty directory
# stays committed.
import shutil as _shutil
if os.path.isdir(OUT_DIR):
    for _e in os.listdir(OUT_DIR):
        if _e == ".gitkeep":
            continue
        _p = os.path.join(OUT_DIR, _e)
        if os.path.isfile(_p) or os.path.islink(_p):
            os.remove(_p)
        else:
            _shutil.rmtree(_p, ignore_errors=True)
os.makedirs(OUT_DIR, exist_ok=True)

BOXES = {
    'nino12':  {'lat': (-10, 0),  'lon': (270, 280)},
    'nino3':   {'lat': (-5,  5),  'lon': (210, 270)},
    'nino34':  {'lat': (-5,  5),  'lon': (190, 240)},
    'nino4':   {'lat': (-5,  5),  'lon': (160, 210)},
}
REGIONS = list(BOXES.keys())

import xarray as xr

def area_weighted_mean(sst_2d, latbox, lonbox):
    sub = sst_2d.sel(lat=slice(latbox[0], latbox[1]),
                     lon=slice(lonbox[0], lonbox[1]))
    w = np.cos(np.deg2rad(sub.lat))
    return float(sub.weighted(w).mean(('lat','lon')).values)

print("[1] .nc 파일에서 박스 평균 SST 추출 중...")
rows = []
for f in sorted(glob.glob(f'{NC_DIR}/oisst-avhrr-v02r01.*.nc')):
    m = re.search(r'(\d{8})', f)
    if not m: continue
    date = pd.to_datetime(m.group(1))
    ds = xr.open_dataset(f)
    sst = ds.sst.squeeze()
    if 'zlev' in sst.dims: sst = sst.squeeze('zlev')
    row = {
        'date': date,
        'file': Path(f).name,
        'preliminary': 'prelim' in f.lower(),
    }
    for name, box in BOXES.items():
        row[name] = area_weighted_mean(sst, box['lat'], box['lon'])
    row['grad_4_minus_12']  = row['nino4']  - row['nino12']
    row['grad_4_minus_3']   = row['nino4']  - row['nino3']
    row['grad_34_minus_12'] = row['nino34'] - row['nino12']
    row['grad_4_minus_34']  = row['nino4']  - row['nino34']
    rows.append(row)
    ds.close()

df = pd.DataFrame(rows).sort_values('date').reset_index(drop=True)
# Nino 체크는 최근 2달치만 — CPC bias / rSSTA / RONI / 주간·월간 집계 모두
# 이 서브셋에서 계산. OISST 캐시는 3개월 유지되지만 분석 대상은 좁힘.
_two_months_cutoff = df.date.max() - pd.DateOffset(months=2)
df = df[df.date >= _two_months_cutoff].reset_index(drop=True)
df['month'] = df.date.dt.month
df['day']   = df.date.dt.day
print(f"    -> {len(df)}일 처리 ({df.date.min().date()} ~ {df.date.max().date()})")

print("[2] Climatology 적용 -> anomaly 계산")
clim = pd.read_csv(CLIM_FILE)
df = df.merge(clim[['month','day'] + [f'{r}_clim' for r in REGIONS]],
              on=['month','day'], how='left')
for r in REGIONS:
    df[f'{r}_anom'] = df[r] - df[f'{r}_clim']

print("[3] CPC 주간 bias 오프셋 계산")
abs_rows = []
pair_re = re.compile(r'(\d{2,3}\.\d)\s*(-?\d+\.\d)')
for ln in open(ABS_FILE):
    m = re.match(r'^\s*(\d{1,2}[A-Z]{3}\d{4})', ln)
    if not m: continue
    try: dt = pd.to_datetime(m.group(1), format='%d%b%Y')
    except: continue
    pairs = pair_re.findall(ln)
    if len(pairs) != 4: continue
    (s12,a12),(s3,a3),(s34,a34),(s4,a4) = pairs
    abs_rows.append({'week_center':dt,
        'nino12_ssta':float(a12),'nino3_ssta':float(a3),
        'nino34_ssta':float(a34),'nino4_ssta':float(a4)})
abs_cpc = pd.DataFrame(abs_rows)

date_min, date_max = df.date.min(), df.date.max()
cpc_rng = abs_cpc[(abs_cpc.week_center >= date_min) &
                  (abs_cpc.week_center <= date_max + pd.Timedelta(days=3))]

bias_rows = []
for _, wk in cpc_rng.iterrows():
    wc = wk['week_center']
    sub = df[(df.date >= wc - pd.Timedelta(days=3)) & (df.date <= wc + pd.Timedelta(days=3))]
    if len(sub) < 4: continue
    row = {'week_center': wc}
    for r in REGIONS:
        row[f'{r}_bias'] = sub[f'{r}_anom'].mean() - wk[f'{r}_ssta']
    bias_rows.append(row)

offsets = {}
if bias_rows:
    bias_df = pd.DataFrame(bias_rows)
    for r in REGIONS:
        offsets[r] = bias_df[f'{r}_bias'].mean()
    print(f"    -> offsets: {offsets}")
else:
    for r in REGIONS: offsets[r] = 0.0
    print("    -> CPC 주간값 부족, offset=0")

for r in REGIONS:
    df[f'{r}_anom_corrected'] = df[f'{r}_anom'] - offsets[r]

print("[4] 열대평균 아노말리 역산 -> rSSTA 계산")
rel_rows = []
for ln in open(REL_FILE):
    m = re.match(r'^\s*(\d{1,2}[A-Z]{3}\d{4})', ln)
    if not m: continue
    try: dt = pd.to_datetime(m.group(1), format='%d%b%Y')
    except: continue
    parts = ln.split()
    if len(parts) < 5: continue
    try: vals = [float(x) for x in parts[1:5]]
    except: continue
    rel_rows.append({'week_center':dt,'n12':vals[0],'n3':vals[1],'n34':vals[2],'n4':vals[3]})
rel = pd.DataFrame(rel_rows)

merged_cpc = abs_cpc.merge(rel, on='week_center', how='inner')
merged_cpc['tropmean'] = ((merged_cpc['nino12_ssta'] - merged_cpc['n12']) +
                         (merged_cpc['nino3_ssta']  - merged_cpc['n3'])  +
                         (merged_cpc['nino34_ssta'] - merged_cpc['n34']) +
                         (merged_cpc['nino4_ssta']  - merged_cpc['n4'])) / 4

from scipy.stats import linregress

N_WEEKS_FIT = 8
trop_recent = merged_cpc.tail(N_WEEKS_FIT).copy().sort_values('week_center').reset_index(drop=True)

daily_idx = pd.date_range(date_min, date_max, freq='D')

if len(trop_recent) >= 2:
    ref_date = trop_recent['week_center'].iloc[0]
    trop_recent['days_from_ref'] = (trop_recent['week_center'] - ref_date).dt.days

    slope, intercept, r_val, p_val, std_err = linregress(
        trop_recent['days_from_ref'], trop_recent['tropmean']
    )
    trop_trend_per_week = slope * 7
    trop_r2 = r_val ** 2
    print(f"    열대평균 선형 추세: {trop_trend_per_week:+.4f} C/주, R2={trop_r2:.3f} (n={len(trop_recent)} weeks)")

    last_obs_date = trop_recent['week_center'].iloc[-1]
    last_obs_val  = trop_recent['tropmean'].iloc[-1]

    trop_daily_vals = []
    for d in daily_idx:
        if d <= last_obs_date:
            after  = trop_recent[trop_recent['week_center'] >= d]
            before = trop_recent[trop_recent['week_center'] <= d]
            if len(after) > 0 and len(before) > 0:
                i_a = after.index[0]
                i_b = before.index[-1]
                if i_a == i_b:
                    val = float(trop_recent.loc[i_a, 'tropmean'])
                else:
                    d_a = trop_recent.loc[i_a, 'days_from_ref']
                    d_b = trop_recent.loc[i_b, 'days_from_ref']
                    v_a = trop_recent.loc[i_a, 'tropmean']
                    v_b = trop_recent.loc[i_b, 'tropmean']
                    days = (d - ref_date).days
                    val = v_b + (v_a - v_b) * (days - d_b) / (d_a - d_b)
            else:
                val = slope * (d - ref_date).days + intercept
        else:
            days_after = (d - last_obs_date).days
            val = last_obs_val + slope * days_after
        trop_daily_vals.append(val)

    trop_daily = pd.DataFrame({'date': daily_idx, 'tropical_mean_ssta': trop_daily_vals})
else:
    print("    [warn] CPC 주간 데이터 부족 - 단일 값으로 fill")
    fallback = float(trop_recent['tropmean'].mean()) if len(trop_recent) > 0 else 0.0
    trop_daily = pd.DataFrame({'date': daily_idx, 'tropical_mean_ssta': [fallback]*len(daily_idx)})
    trop_trend_per_week = 0.0
    trop_r2 = float('nan')

df = df.merge(trop_daily, on='date', how='left')

for r in REGIONS:
    df[f'{r}_rssta'] = df[f'{r}_anom_corrected'] - df['tropical_mean_ssta']

# RONI index = rSSTA(Niño 3) × K_month, K from RONI_K_monthly.csv
# (CPC ERSSTv5 1991–2020). Column name kept as `nino34_roni` for
# downstream CSV/report stability.
roni_k = pd.read_csv(RONI_K_FILE)[['month', 'K']]
df = df.merge(roni_k, on='month', how='left')
df['nino34_roni'] = df['nino3_rssta'] * df['K']

clim_idx = clim.set_index(['month','day'])
df = df.set_index(['month','day'])
df['grad_4_12_clim']   = clim_idx['nino4_clim']  - clim_idx['nino12_clim']
df['grad_4_3_clim']    = clim_idx['nino4_clim']  - clim_idx['nino3_clim']
df['grad_34_12_clim']  = clim_idx['nino34_clim'] - clim_idx['nino12_clim']
df['grad_4_34_clim']   = clim_idx['nino4_clim']  - clim_idx['nino34_clim']
df = df.reset_index()
df['grad_4_12_anom']   = df['grad_4_minus_12']  - df['grad_4_12_clim']
df['grad_4_3_anom']    = df['grad_4_minus_3']   - df['grad_4_3_clim']
df['grad_34_12_anom']  = df['grad_34_minus_12'] - df['grad_34_12_clim']
df['grad_4_34_anom']   = df['grad_4_minus_34']  - df['grad_4_34_clim']

df.to_csv(f'{CSV_DIR}/nino_rssta_daily.csv', index=False)
print(f"    -> {CSV_DIR}/nino_rssta_daily.csv 저장")

print("[5] 주별 평균 계산 (일~토)")
def week_start_sun(d):
    wd = d.weekday()
    return d - pd.Timedelta(days=(wd+1) % 7)

df['week_start']  = df.date.apply(week_start_sun)
df['week_center'] = df.week_start + pd.Timedelta(days=3)

agg = {'week_center':('week_center','first'), 'n_days':('date','count')}
for r in REGIONS:
    agg[f'{r}_rssta']      = (f'{r}_rssta','mean')
    agg[f'{r}_anom']       = (f'{r}_anom_corrected','mean')
    agg[r]                 = (r,'mean')
agg['nino34_roni']    = ('nino34_roni','mean')
agg['grad_4_12_abs']  = ('grad_4_minus_12','mean')
agg['grad_4_12_anom'] = ('grad_4_12_anom','mean')

weekly = df.groupby('week_start').agg(**agg).reset_index()
weekly = weekly.merge(rel.rename(columns={'week_center':'week_center'}),
                     on='week_center', how='left')
weekly.to_csv(f'{CSV_DIR}/nino_rssta_weekly.csv', index=False)
print(f"    -> {CSV_DIR}/nino_rssta_weekly.csv 저장")

print("[5b] 월별 평균 + CPC 월간 (sstoi.indices, rel_mthsst9120) 비교")
# CPC sstoi.indices: "YR MON NINO1+2 ANOM NINO3 ANOM NINO4 ANOM NINO3.4 ANOM"
mth_abs_rows = []
for ln in open(MTH_ABS_FILE):
    parts = ln.split()
    if len(parts) < 10:
        continue
    try:
        yr = int(parts[0]); mo = int(parts[1])
        vals = [float(x) for x in parts[2:10]]
    except ValueError:
        continue
    mth_abs_rows.append({
        'year': yr, 'month': mo,
        'nino12_cpc':       vals[0], 'nino12_anom_cpc': vals[1],
        'nino3_cpc':        vals[2], 'nino3_anom_cpc':  vals[3],
        'nino4_cpc':        vals[4], 'nino4_anom_cpc':  vals[5],
        'nino34_cpc':       vals[6], 'nino34_anom_cpc': vals[7],
    })
mth_abs = pd.DataFrame(mth_abs_rows)

# CPC rel_mthsst9120.txt: "YEAR MON rNINO1+2 rNINO3 rNINO4 rNINO3.4"
mth_rel_rows = []
for ln in open(MTH_REL_FILE):
    parts = ln.split()
    if len(parts) < 6:
        continue
    try:
        yr = int(parts[0]); mo = int(parts[1])
        vals = [float(x) for x in parts[2:6]]
    except ValueError:
        continue
    mth_rel_rows.append({
        'year': yr, 'month': mo,
        'nino12_rssta_cpc': vals[0],
        'nino3_rssta_cpc':  vals[1],
        'nino4_rssta_cpc':  vals[2],
        'nino34_rssta_cpc': vals[3],
    })
mth_rel = pd.DataFrame(mth_rel_rows)
cpc_monthly = mth_abs.merge(mth_rel, on=['year','month'], how='outer')

# OISST monthly aggregates from daily df. df['month'] already exists (1-12);
# add year so we can group.
oisst_mth_agg = {'n_days': ('date', 'count')}
for r in REGIONS:
    oisst_mth_agg[f'{r}_oisst']       = (r, 'mean')
    oisst_mth_agg[f'{r}_anom_oisst']  = (f'{r}_anom_corrected', 'mean')
    oisst_mth_agg[f'{r}_rssta_oisst'] = (f'{r}_rssta', 'mean')
oisst_mth_agg['nino34_roni_oisst'] = ('nino34_roni', 'mean')
oisst_mth_agg['K']                 = ('K', 'first')

oisst_mth = (df.assign(year=df.date.dt.year)
               .groupby(['year', 'month'])
               .agg(**oisst_mth_agg)
               .reset_index())

monthly = oisst_mth.merge(cpc_monthly, on=['year', 'month'], how='left')
# Days-in-month for status column
monthly['days_in_month'] = monthly.apply(
    lambda r: pd.Period(f"{int(r.year)}-{int(r.month):02d}", freq='M').days_in_month,
    axis=1,
)
monthly.to_csv(f'{CSV_DIR}/nino_rssta_monthly.csv', index=False)
print(f"    -> {CSV_DIR}/nino_rssta_monthly.csv 저장 ({len(monthly)} months)")

print("[6] 플롯 생성")
rel_recent = rel.rename(columns={'week_center':'date'})
rel_recent = rel_recent[rel_recent['date'] >= date_min - pd.Timedelta(days=7)]

plot_cfg = [
    ('nino12','Nino 1+2','#d62728','n12'),
    ('nino3', 'Nino 3',  '#ff7f0e','n3'),
    ('nino34','Nino 3.4','#2ca02c','n34'),
    ('nino4', 'Nino 4',  '#1f77b4','n4'),
]

fig, axes = plt.subplots(4, 1, figsize=(12, 13), sharex=True)
for ax, (key, label, color, ccol) in zip(axes, plot_cfg):
    for _, wk in weekly.iterrows():
        ws = wk['week_start']
        we = ws + pd.Timedelta(days=6)
        y = wk[f'{key}_rssta']
        complete = wk['n_days'] == 7
        ax.hlines(y, ws, we, colors=color, linewidth=3,
                  alpha=0.55 if complete else 0.3,
                  linestyle='-' if complete else '--')
        ax.annotate(f'{y:+.2f}' + ('' if complete else '*'),
                    xy=(ws + pd.Timedelta(days=3), y), xytext=(0, 7),
                    textcoords='offset points', ha='center', va='bottom',
                    fontsize=8.5, color=color, fontweight='bold')
    ax.plot(df.date, df[f'{key}_rssta'], '-', color=color, lw=1.3, alpha=0.8, label='Daily')
    fmask = ~df.preliminary
    pmask = df.preliminary
    ax.scatter(df.loc[fmask,'date'], df.loc[fmask,f'{key}_rssta'],
               color=color, s=35, zorder=5, edgecolors='white', lw=0.8, label='Final')
    ax.scatter(df.loc[pmask,'date'], df.loc[pmask,f'{key}_rssta'],
               color='white', edgecolors=color, s=35, zorder=5, lw=1.3, label='Prelim')
    ax.scatter(rel_recent.date, rel_recent[ccol],
               color='black', s=120, marker='x', zorder=6, lw=2.5, label='CPC weekly')
    for ws in weekly.week_start:
        ax.axvline(ws, color='gray', lw=0.5, alpha=0.3, ls=':')
    ax.axhline(0, color='gray', lw=0.6)
    ax.axhline(0.5, color='red', lw=0.5, ls=':', alpha=0.5)
    ax.axhline(-0.5, color='blue', lw=0.5, ls=':', alpha=0.5)
    last = df.iloc[-1]
    ax.annotate(f' {last[f"{key}_rssta"]:+.2f}',
                xy=(last.date, last[f'{key}_rssta']), xytext=(6,0),
                textcoords='offset points', fontsize=10, fontweight='bold',
                color=color, va='center')
    ax.set_title(label, loc='left', fontsize=11, fontweight='bold')
    ax.set_ylabel('rSSTA (C)')
    ax.grid(True, alpha=0.2)
    if ax == axes[0]:
        ax.legend(loc='upper left', fontsize=8, framealpha=0.9, ncol=2)

def _day_or_month(x, pos=None):
    """X-axis tick formatter: day number on main line; month name below only
    on the 1st of the month (and on the leftmost tick). Prevents overlap
    at the ~2-month span."""
    d = mdates.num2date(x)
    if d.day == 1 or pos == 0:
        return f"{d.day}\n{d.strftime('%b')}"
    return f"{d.day}"

axes[-1].set_xlabel('Date')
axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=5))
axes[-1].xaxis.set_major_formatter(plt.FuncFormatter(_day_or_month))
fig.suptitle(f'Daily rSSTA (raw, no variance correction) + Weekly averages -- '
             f'updated through {df.date.max().strftime("%Y-%m-%d")}',
             fontsize=12, y=0.995)
plt.tight_layout()
plt.savefig(f'{OUT_DIR}/nino_rssta_plot.png', dpi=140, bbox_inches='tight')
plt.close()

fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
ax = axes[0]
ax.plot(df.date, df.grad_4_minus_12,  '-o', color='#8B4513', lw=1.8, ms=5, label='Nino 4 - Nino 1+2')
ax.plot(df.date, df.grad_34_minus_12, '-o', color='#228B22', lw=1.5, ms=5, label='Nino 3.4 - Nino 1+2')
ax.plot(df.date, df.grad_4_12_clim,  '--', color='#8B4513', lw=1.5, alpha=0.5, label='1991-2020 clim (4-1+2)')
ax.plot(df.date, df.grad_34_12_clim, '--', color='#228B22', lw=1.5, alpha=0.5, label='1991-2020 clim (34-1+2)')
ax.set_title('Zonal SST gradients -- absolute vs climatology', loc='left', fontsize=11, fontweight='bold')
ax.set_ylabel('dSST (C)'); ax.grid(True, alpha=0.3)
ax.legend(loc='upper left', fontsize=9, ncol=2)

ax = axes[1]
ax.plot(df.date, df.grad_4_12_anom,  '-o', color='#8B4513', lw=1.8, ms=5, label='grad(4-1+2) anom')
ax.plot(df.date, df.grad_34_12_anom, '-o', color='#228B22', lw=1.5, ms=5, label='grad(3.4-1+2) anom')
ax.axhline(0, color='gray', lw=0.6)
ax.fill_between(df.date, 0, df.grad_4_12_anom, where=(df.grad_4_12_anom<0),
                color='red', alpha=0.15, label='weaker gradient (->El Nino-like)')
ax.set_title('Gradient anomaly -- negative = weaker trade-wind forcing',
             loc='left', fontsize=11, fontweight='bold')
ax.set_ylabel('dSST anomaly (C)'); ax.grid(True, alpha=0.3)
ax.legend(loc='lower right', fontsize=9)

last = df.iloc[-1]
for ax_, val, color in [(axes[0], last.grad_4_minus_12, '#8B4513'),
                         (axes[0], last.grad_34_minus_12, '#228B22'),
                         (axes[1], last.grad_4_12_anom, '#8B4513'),
                         (axes[1], last.grad_34_12_anom, '#228B22')]:
    ax_.annotate(f' {val:+.2f}', xy=(last.date, val), xytext=(6,0),
                 textcoords='offset points', fontsize=9, fontweight='bold',
                 color=color, va='center')

axes[-1].set_xlabel('Date')
axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=5))
axes[-1].xaxis.set_major_formatter(plt.FuncFormatter(_day_or_month))
fig.suptitle(f'Equatorial Pacific Zonal SST Gradient -- through {df.date.max().strftime("%Y-%m-%d")}',
             fontsize=12, y=0.995)
plt.tight_layout()
plt.savefig(f'{OUT_DIR}/nino_gradients_plot.png', dpi=140, bbox_inches='tight')
plt.close()
print(f"    -> 플롯 2개 저장")

print("[7] 보고서 생성")

def classify(rssta):
    if   rssta >=  1.0:  return "Very Strong Warm"
    elif rssta >=  0.65: return "Strong Warm"
    elif rssta >=  0.33: return "El Nino threshold (raw scale)"
    elif rssta >=  0.2:  return "Warm-leaning"
    elif rssta >  -0.2:  return "Neutral"
    elif rssta > -0.33:  return "Cool-leaning"
    elif rssta > -0.65:  return "La Nina threshold (raw scale)"
    else:                return "Strong Cold"

last = df.iloc[-1]
latest_date = last.date.strftime('%Y-%m-%d')

d7 = df[df.date >= last.date - pd.Timedelta(days=7)]
if len(d7) >= 2:
    trend = {r: d7.iloc[-1][f'{r}_rssta'] - d7.iloc[0][f'{r}_rssta'] for r in REGIONS}
else:
    trend = {r: 0 for r in REGIONS}

latest_cpc_week = rel[rel.week_center <= last.date].tail(1)
cpc_latest_str = ''
if len(latest_cpc_week) > 0:
    wk = latest_cpc_week.iloc[0]
    wc = wk.week_center
    cpc_latest_str = f"\n최근 CPC 공식 주간 rSSTA ({wc.strftime('%Y-%m-%d')} centered week):\n"
    cpc_latest_str += "| Region | CPC rSSTA |\n|---|---|\n"
    for r, ccol in [('nino12','n12'),('nino3','n3'),('nino34','n34'),('nino4','n4')]:
        cpc_latest_str += f"| {r} | {wk[ccol]:+.2f} |\n"

wk_table = "| Week (Sun) | Days | Nino 1+2 | Nino 3 | **Nino 3.4** | Nino 4 | grad(4-1+2) |\n"
wk_table += "|---|---|---|---|---|---|---|\n"
for _, w in weekly.iterrows():
    status = 'OK' if w.n_days == 7 else f'{int(w.n_days)}/7'
    wk_table += (f"| {w.week_start.strftime('%m-%d')} | {status} | "
                 f"{w.nino12_rssta:+.2f} | {w.nino3_rssta:+.2f} | "
                 f"**{w.nino34_rssta:+.2f}** | {w.nino4_rssta:+.2f} | "
                 f"{w.grad_4_12_abs:+.2f} ({w.grad_4_12_anom:+.2f}) |\n")

latest_table = "| Region | rSSTA (raw, K=1) | Status | 7-day trend |\n|---|---|---|---|\n"
for r, name in [('nino12','Nino 1+2'),('nino3','Nino 3'),
                ('nino34','Nino 3.4'),('nino4','Nino 4')]:
    v = last[f'{r}_rssta']
    t = trend[r]
    arrow = 'up' if t > 0.1 else ('dn' if t < -0.1 else '->')
    latest_table += f"| {name} | {v:+.2f} | {classify(v)} | {arrow} {t:+.2f} |\n"

# 7-day RONI trend (Niño 3.4)
roni_trend = 0.0
if len(d7) >= 2:
    roni_trend = float(d7.iloc[-1]['nino34_roni'] - d7.iloc[0]['nino34_roni'])

def classify_roni(v):
    if   v >=  0.5: return "El Niño (RONI ≥ +0.5)"
    elif v >=  0.2: return "Warm-leaning"
    elif v >  -0.2: return "Neutral"
    elif v >  -0.5: return "Cool-leaning"
    else:           return "La Niña (RONI ≤ −0.5)"

n3_val   = last.nino3_rssta
n34_roni = last.nino34_roni
n34_K    = last.K
if n34_roni >= 0.5:
    verdict = (f"**RONI = {n34_roni:+.2f} (rSSTA(3) {n3_val:+.2f} × K={n34_K:.3f}) "
               f"— El Niño threshold crossed (≥ +0.5)** "
               f"(공식 선언은 5개 연속 3개월 평균 ≥ +0.5 기준)")
elif n34_roni >= 0.2:
    verdict = (f"**Warm-leaning** (RONI {n34_roni:+.2f}, rSSTA(3) {n3_val:+.2f} × K={n34_K:.3f}) "
               f"— El Niño 발달 초기 신호 가능")
elif n34_roni > -0.2:
    verdict = f"**ENSO-neutral** (RONI {n34_roni:+.2f}, rSSTA(3) {n3_val:+.2f} × K={n34_K:.3f})"
elif n34_roni > -0.5:
    verdict = (f"**Cool-leaning** (RONI {n34_roni:+.2f}, rSSTA(3) {n3_val:+.2f} × K={n34_K:.3f}) "
               f"— La Niña 발달 신호 가능")
else:
    verdict = (f"**RONI = {n34_roni:+.2f} (rSSTA(3) {n3_val:+.2f} × K={n34_K:.3f}) "
               f"— La Niña threshold crossed (≤ −0.5)**")

cp_ep = ""
n12_v = last.nino12_rssta
n34_v = last.nino34_rssta
if n34_v > 0.3 and n12_v < n34_v - 0.3:
    cp_ep = "\n**Modoki (CP형) 경향**: Nino 3.4 > Nino 1+2 -> 중앙태평양 중심 warming"
elif n12_v > 0.3 and n12_v > n34_v + 0.3:
    cp_ep = "\n**Eastern Pacific (EP형) 경향**: Nino 1+2 > Nino 3.4 -> 동태평양 중심 warming"

g_anom = last.grad_4_12_anom
if g_anom < -0.5:
    grad_desc = f"현재 경사 편차 {g_anom:+.2f}C -> 평년보다 **경사 약함** -> 무역풍 약화 가능성"
elif g_anom > 0.5:
    grad_desc = f"현재 경사 편차 {g_anom:+.2f}C -> 평년보다 **경사 강함** -> 무역풍 강화 (La Nina-like)"
else:
    grad_desc = f"현재 경사 편차 {g_anom:+.2f}C -> 평년 수준"

validation_str = ""
if len(bias_rows) > 0:
    validation_str = "\n### CPC 대비 검증 (주간 평균 기준)\n\n"
    validation_str += "| Region | Mean bias (C) |\n|---|---|\n"
    for r, name in [('nino12','Nino 1+2'),('nino3','Nino 3'),
                    ('nino34','Nino 3.4'),('nino4','Nino 4')]:
        validation_str += f"| {name} | {offsets[r]:+.3f} |\n"

# ─── RONI section (Niño 3.4): daily/weekly/monthly with K applied ───────────
def _fmt(v):
    return f"{v:+.2f}" if pd.notna(v) else " -- "

roni_str = "\n## RONI — rSSTA(Niño 3) × K_month\n\n"
roni_str += (
    "RONI = rSSTA(3) × K, where K is the CPC monthly variance scaling "
    "(K = σ(rNINO3.4) / σ(rN34_raw), 1991–2020 baseline, ERSSTv5). K peaks "
    "in Sep (~1.27) and bottoms in Mar (~1.08). Column name kept as "
    "`nino34_roni` for CSV/downstream stability. Standard ENSO thresholds "
    "(±0.5 °C) applied directly.\n\n"
)
arrow_r = 'up' if roni_trend > 0.05 else ('dn' if roni_trend < -0.05 else '->')
roni_str += (
    f"### Latest daily ({latest_date})\n"
    f"- rSSTA(3) = **{n3_val:+.2f}**\n"
    f"- K ({last.date.strftime('%b')}) = {n34_K:.3f}\n"
    f"- **RONI = {n34_roni:+.2f}** — {classify_roni(n34_roni)}  "
    f"(7-day trend {arrow_r} {roni_trend:+.2f})\n\n"
)

# Recent weeks RONI table
roni_str += "### Recent weeks\n\n"
roni_str += "| Week (Sun) | Days | rSSTA(3) | K (week) | **RONI** |\n"
roni_str += "|---|---|---|---|---|\n"
weekly_k = df.groupby('week_start')['K'].mean().reset_index().rename(columns={'K':'K_week'})
weekly_disp = weekly.merge(weekly_k, on='week_start', how='left')
for _, w in weekly_disp.iterrows():
    days = 'OK' if int(w['n_days']) == 7 else f"{int(w['n_days'])}/7"
    roni_str += (
        f"| {w['week_start'].strftime('%m-%d')} | {days} | "
        f"{w['nino3_rssta']:+.2f} | {w['K_week']:.3f} | "
        f"**{w['nino34_roni']:+.2f}** |\n"
    )

# Monthly RONI table
roni_str += "\n### Monthly\n\n"
roni_str += "| Month | Days | rSSTA(3) | K | **RONI** | Status |\n"
roni_str += "|---|---|---|---|---|---|\n"
for _, mr in monthly.sort_values(['year','month']).iterrows():
    ym = f"{int(mr['year']):04d}-{int(mr['month']):02d}"
    days = 'OK' if int(mr['n_days']) == int(mr['days_in_month']) else f"{int(mr['n_days'])}/{int(mr['days_in_month'])}"
    roni_str += (
        f"| {ym} | {days} | {mr['nino3_rssta_oisst']:+.2f} | "
        f"{mr['K']:.3f} | **{mr['nino34_roni_oisst']:+.2f}** | "
        f"{classify_roni(mr['nino34_roni_oisst'])} |\n"
    )

# ─── Monthly comparison table (OISST vs CPC sstoi.indices + rel_mthsst9120) ───

monthly_str = ""
if len(monthly) > 0:
    mth_sorted = monthly.sort_values(['year','month'])
    # Nino 3.4 focus (matches the rest of the report)
    monthly_str  = "\n## Monthly comparison — Niño 3.4 (OISST vs CPC)\n\n"
    monthly_str += ("OISST monthly mean from daily .nc; CPC monthly from "
                    "`sstoi.indices` (ERSSTv5 ANOM) + `rel_mthsst9120.txt` (rNINO3.4).\n\n")
    monthly_str += ("| Month | Days | OISST rSSTA | CPC rNINO3.4 | OISST anom | CPC ANOM | OISST SST | CPC SST |\n"
                    "|---|---|---|---|---|---|---|---|\n")
    for _, mr in mth_sorted.iterrows():
        ym = f"{int(mr['year']):04d}-{int(mr['month']):02d}"
        status = 'OK' if int(mr['n_days']) == int(mr['days_in_month']) else f"{int(mr['n_days'])}/{int(mr['days_in_month'])}"
        monthly_str += (
            f"| {ym} | {status} | "
            f"{_fmt(mr['nino34_rssta_oisst'])} | {_fmt(mr.get('nino34_rssta_cpc'))} | "
            f"{_fmt(mr['nino34_anom_oisst'])} | {_fmt(mr.get('nino34_anom_cpc'))} | "
            f"{_fmt(mr['nino34_oisst'])} | {_fmt(mr.get('nino34_cpc'))} |\n"
        )

    # Per-region mean bias on months that have both sources
    full_mths = mth_sorted.dropna(subset=['nino34_rssta_cpc'])
    if len(full_mths) > 0:
        monthly_str += "\n### Monthly OISST−CPC bias (rSSTA, all overlap months)\n\n"
        monthly_str += "| Region | OISST−CPC rSSTA mean | n months |\n|---|---|---|\n"
        for r, name in [('nino12','Nino 1+2'),('nino3','Nino 3'),
                        ('nino34','Nino 3.4'),('nino4','Nino 4')]:
            diff = (full_mths[f'{r}_rssta_oisst'] - full_mths[f'{r}_rssta_cpc']).dropna()
            if len(diff) > 0:
                monthly_str += f"| {name} | {diff.mean():+.3f} | {len(diff)} |\n"

report = f"""# ENSO Daily Monitoring Report
**Date**: {latest_date}
**Data**: OISST v2.1 AVHRR, {len(df)} days from {df.date.min().strftime('%Y-%m-%d')}
**Reference**: 1991-2020 climatology (CPC), tropical mean (20S-20N) subtracted. \\
**rSSTA** = raw relative SSTA (no variance correction, K=1). **RONI** = rSSTA(3) × K_month (CPC ERSSTv5 1991-2020 monthly K), compared to ±0.5 °C ENSO thresholds. See RONI section below.
**Tropical mean trend**: {trop_trend_per_week:+.4f} C/week (R2={trop_r2:.2f}, last {N_WEEKS_FIT} weeks)

---

## Verdict
{verdict}
{cp_ep}

---

## Latest Daily rSSTA ({latest_date})
{latest_table}

## Zonal Gradient
- **Nino 4 - Nino 1+2**: {last.grad_4_minus_12:+.2f}C (clim {last.grad_4_12_clim:+.2f}, anom {last.grad_4_12_anom:+.2f})
- **Nino 3.4 - Nino 1+2**: {last.grad_34_minus_12:+.2f}C (clim {last.grad_34_12_clim:+.2f}, anom {last.grad_34_12_anom:+.2f})

{grad_desc}

---

## Weekly summary (Sunday-start weeks)
{wk_table}
{cpc_latest_str}

{validation_str}

---
{roni_str}

---
{monthly_str}

---

## Output files
- `nino_rssta_daily.csv` -- full daily data (incl. `nino34_roni`, K)
- `nino_rssta_weekly.csv` -- weekly summary (incl. `nino34_roni`)
- `nino_rssta_monthly.csv` -- monthly OISST vs CPC + `nino34_roni_oisst`, K
- `nino_rssta_plot.png` -- rSSTA time series
- `nino_gradients_plot.png` -- zonal gradient

*Generated automatically from OISST .nc files + CPC weekly + monthly references + RONI K (CPC ERSSTv5).*
"""

with open(f'{OUT_DIR}/nino_report.md', 'w', encoding='utf-8') as f:
    f.write(report)
print(f"    -> {OUT_DIR}/nino_report.md 저장")

# ────── [8] Indo-Pacific 격자 SST CSV (latest day only, ±0.5° mean) ──────
# Western Pacific + Indian Ocean, lat -10° to +10°. Each output cell is the
# mean of OISST values within a ±0.5° box around the cell center, i.e. a
# 1°×1° grid sampled on integer lat/lon points (lat: -10..10, lon: 30..180).
# Single-snapshot for the most recent date.
print("[8] Indo-Pacific 격자 SST CSV (최근 1일, ±0.5° 평균)")
INDO_PACIFIC_LAT = (-20.0, 20.0)
INDO_PACIFIC_LON = (30.0, 180.0)
HALF_BOX = 0.5  # ±0.5° → 1°×1° cell centred on integer lat/lon
nc_files = sorted(glob.glob(f'{NC_DIR}/oisst-avhrr-v02r01.*.nc'))
if not nc_files:
    print("    -> .nc 파일 없음, skip")
else:
    latest_nc = nc_files[-1]
    latest_date = pd.to_datetime(re.search(r'(\d{8})', latest_nc).group(1))
    ds_latest = xr.open_dataset(latest_nc)
    sst_latest = ds_latest.sst.squeeze()
    if 'zlev' in sst_latest.dims:
        sst_latest = sst_latest.squeeze('zlev')
    # ±0.5° box mean per integer (lat, lon) cell. cos(lat)-weighted so
    # the box mean reflects equal-area averaging.
    rows = []
    lat_centers = np.arange(INDO_PACIFIC_LAT[0], INDO_PACIFIC_LAT[1] + 1, 1.0)
    lon_centers = np.arange(INDO_PACIFIC_LON[0], INDO_PACIFIC_LON[1] + 1, 1.0)
    for lat_c in lat_centers:
        for lon_c in lon_centers:
            sub = sst_latest.sel(
                lat=slice(lat_c - HALF_BOX, lat_c + HALF_BOX),
                lon=slice(lon_c - HALF_BOX, lon_c + HALF_BOX),
            )
            if sub.size == 0:
                continue
            w = np.cos(np.deg2rad(sub.lat))
            try:
                val = float(sub.weighted(w).mean(('lat', 'lon')).values)
            except Exception:
                val = float('nan')
            if not np.isnan(val):
                rows.append({'lat': lat_c, 'lon': lon_c, 'sst': val})
    grid_df = pd.DataFrame(rows)
    grid_df.insert(0, 'date', latest_date.strftime('%Y-%m-%d'))
    grid_csv = f'{CSV_DIR}/sst_grid_indo_pacific.csv'
    grid_df.to_csv(grid_csv, index=False, float_format='%.3f')
    ds_latest.close()
    print(f"    -> {grid_csv}  ({len(grid_df):,} rows, "
          f"{grid_df['lat'].nunique()} lats × {grid_df['lon'].nunique()} lons, "
          f"date={latest_date.date()})")

print("\n" + "="*60)
print(f"완료: 분석 기간 {df.date.min().strftime('%Y-%m-%d')} ~ {df.date.max().strftime('%Y-%m-%d')}")
print("="*60)
print(report)

try:
    import zipfile
    zip_path = Path(OUT_DIR) / 'nino_analysis.zip'
    csv_extras = [
        Path(CSV_DIR) / 'nino_rssta_daily.csv',
        Path(CSV_DIR) / 'nino_rssta_weekly.csv',
        Path(CSV_DIR) / 'nino_rssta_monthly.csv',
        Path(CSV_DIR) / 'sst_grid_indo_pacific.csv',
    ]
    with zipfile.ZipFile(zip_path, 'w',
                          compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # Everything that lives in OUT_DIR (plots, report.md)
        for f in Path(OUT_DIR).iterdir():
            if f.is_file() and f.name not in ('.gitkeep', 'nino_analysis.zip'):
                zf.write(f, arcname=f.name)
        # Plus the CSVs that now live in Output/ root
        for f in csv_extras:
            if f.exists():
                zf.write(f, arcname=f.name)
    print(f"\n-> {zip_path} 생성 완료")
except Exception as e:
    print(f"\nzip 생성 실패: {e}")
