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

NC_DIR    = str(INPUT_DIR / "oisst_data")
CLIM_FILE = str(INPUT_DIR / "nino_clim_daily_1991-2020.csv")
ABS_FILE  = str(INPUT_DIR / "wksst9120.for.txt")
REL_FILE  = str(INPUT_DIR / "rel_wksst9120.txt")
OUT_DIR   = str(OUT_DIR)

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

df.to_csv(f'{OUT_DIR}/nino_rssta_daily.csv', index=False)
print(f"    -> {OUT_DIR}/nino_rssta_daily.csv 저장")

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
agg['grad_4_12_abs']  = ('grad_4_minus_12','mean')
agg['grad_4_12_anom'] = ('grad_4_12_anom','mean')

weekly = df.groupby('week_start').agg(**agg).reset_index()
weekly = weekly.merge(rel.rename(columns={'week_center':'week_center'}),
                     on='week_center', how='left')
weekly.to_csv(f'{OUT_DIR}/nino_rssta_weekly.csv', index=False)
print(f"    -> {OUT_DIR}/nino_rssta_weekly.csv 저장")

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

axes[-1].set_xlabel('Date')
axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=2))
axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%m-%d\n%a'))
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
axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=2))
axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%m-%d\n%a'))
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

n34_val = last.nino34_rssta
if n34_val >= 0.33:
    verdict = (f"**Nino 3.4 raw rSSTA = {n34_val:+.2f} -- El Nino threshold crossed (raw scale)** "
               f"(공식 RONI/ONI는 3개월 이동평균 기준이라 즉시 선언은 아님; raw rSSTA는 분산이 작아 직접 비교 부정확)")
elif n34_val >= 0.20:
    verdict = f"**Warm-leaning** (raw rSSTA {n34_val:+.2f}) -- El Nino 발달 초기 신호 가능"
elif n34_val > -0.20:
    verdict = f"**ENSO-neutral** (raw rSSTA {n34_val:+.2f})"
elif n34_val > -0.33:
    verdict = f"**Cool-leaning** (raw rSSTA {n34_val:+.2f}) -- La Nina 발달 신호 가능"
else:
    verdict = f"**La Nina threshold crossed (raw scale)** (raw rSSTA {n34_val:+.2f})"

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

report = f"""# ENSO Daily Monitoring Report
**Date**: {latest_date}
**Data**: OISST v2.1 AVHRR, {len(df)} days from {df.date.min().strftime('%Y-%m-%d')}
**Reference**: 1991-2020 climatology (CPC), tropical mean (20S-20N) subtracted (K=1, no variance correction). \\
Note: ONI/RONI thresholds (+/-0.5C) are not directly comparable -- RONI's K factor is ERSSTv5-based and requires OISST->ERSST bias correction before applying to OISST data.
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

## Output files
- `nino_rssta_daily.csv` -- full daily data
- `nino_rssta_weekly.csv` -- weekly summary
- `nino_rssta_plot.png` -- rSSTA time series
- `nino_gradients_plot.png` -- zonal gradient

*Generated automatically from OISST .nc files + CPC weekly references.*
"""

with open(f'{OUT_DIR}/nino_report.md', 'w', encoding='utf-8') as f:
    f.write(report)
print(f"    -> {OUT_DIR}/nino_report.md 저장")

print("\n" + "="*60)
print(f"완료: 분석 기간 {df.date.min().strftime('%Y-%m-%d')} ~ {df.date.max().strftime('%Y-%m-%d')}")
print("="*60)
print(report)

try:
    import shutil
    zip_base = str(Path(OUT_DIR) / 'nino_analysis')
    shutil.make_archive(zip_base, 'zip', OUT_DIR)
    print(f"\n-> {zip_base}.zip 생성 완료")
except Exception as e:
    print(f"\nzip 생성 실패: {e}")
