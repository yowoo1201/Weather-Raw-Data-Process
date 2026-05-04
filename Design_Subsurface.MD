# Subsurface Daily Pipeline — Design Document

TAO/TRITON Pacific subsurface daily section pipeline. Roman ASCII raw data를
파싱해 ±5° 위도밴드 daily section을 만들고, per-longitude DOY climatology와
비교한 anomaly·absolute PNG와 zip을 만든다.

원본 노트북: `enso_buoy_doy_climatology.ipynb` (Colab) → 로컬 실행본
[subsurface/run_subsurface.py](subsurface/run_subsurface.py).

---

## 1. Goal & Scope

### Goal
- PMEL TAO/TRITON 부이 ASCII에서 적도 Pacific (±5°) **일별 cross-section** 산출
- per-longitude DOY climatology와 비교해 **anomaly section** 산출
- CPC GODAS 스타일 anomaly PNG 및 absolute temperature PNG 생성 (15일치)
- 원본 일별 데이터 CSV + 모든 PNG를 zip으로 패키징

### Out of scope
- ENSO 지표 산출 (rSSTA, ONI 등) — `sst/` 파이프라인 담당
- ±5° 외 다른 위도밴드 출력 (notebook의 다른 밴드는 비활성화)
- SLA, OLR 같은 다른 동적 변수
- 부이 climatology builder 자체 (이 파이프라인은 미리 만들어진 tarball을 사용)

---

## 2. Inputs

| File | Source | Role |
|---|---|---|
| `Input/data.tar` | PMEL DDS (`*_dy.ascii.gz`) | 최근 daily raw buoy 관측 (±15일 window) |
| `Input/data_p_clim_processed.tar.gz` | climatology builder 출력 | per-longitude DOY 365일 climatology |

### Raw data source
- PMEL TAO/TRITON 다운로드 페이지: <https://www.pmel.noaa.gov/tao/data_deliv/deliv-nojava-all.html>
- raw tarball은 **`data.tar`**로 명명. 이전 이름 `data_p.tar`는 더 이상 사용하지 않음.

### Tarball 구조 (참고)
- `data.tar` 안: `t<lat><n|s><lon><e|w>_dy.ascii.gz` (예: `t5n140w_dy.ascii.gz`)
- `data_p_clim_processed.tar.gz` 안: `t<lon><e|w>_clim.ascii.gz` (예: `t140w_clim.ascii.gz`)

압축 해제는 스크립트가 자동 처리한다 (tar → 디렉터리, `.gz` → 평문 ASCII).

---

## 3. Directory Layout

```
Code/
├── Input/
│   ├── data.tar
│   └── data_p_clim_processed.tar.gz
├── Output/
│   └── Subsurface/                  ← 모든 산출물이 여기로
│       ├── pac_5_anomaly_YYYYMMDD.png
│       ├── pac_5_absolute_YYYYMMDD.png
│       ├── pac_5_data.csv
│       └── pac_5_plots.zip
├── subsurface/
│   ├── run_subsurface.py            ← 실행 스크립트
│   └── pmel_data/                   ← 추출된 ASCII 작업 디렉터리 (자동 생성)
│       ├── pacific_now/
│       └── pacific_clim/
└── Design_Subsurface.MD
```

스크립트는 자기 위치(`__file__`)를 기준으로 경로를 계산하므로 실행 위치와 무관하게 동작한다.

---

## 4. Pipeline Stages

```
Input/data.tar ──► [1] extract  ──► [3] PMEL ASCII parse ──► [5] ±5° band weighted mean
                                                                            │
Input/*clim*.tar.gz ► [1] extract ► [4] climatology load ─► [6] anomaly attach
                                                                            │
                                       [7] section grid ◄───────────────────┤
                                                                            │
                                       [8] per-day PNG  ◄───────────────────┤
                                                                            │
                                       [9] CSV + zip    ◄───────────────────┘
```

### [1] Extraction
- `Input/data.tar` → `subsurface/pmel_data/pacific_now/`
- `Input/data_p_clim_processed.tar.gz` → `subsurface/pmel_data/pacific_clim/`
- 내부 `.gz`는 자동 gunzip. 입력 tarball이 없으면 `FileNotFoundError`.sub

### [3] PMEL DDS ASCII parsing (raw)
- 파일명에서 `(lat, lon360)` 추출
- 다중 블록(블록당 다른 depth set) 파싱: `Depth(M):` 라인으로 depth 시작, `YYYYMMDD` 헤더에서 데이터 시작
- Quality flag ≤ 3만 채택, T ∈ (-9, 40) 범위 검증
- 출력: `time, latitude, longitude, depth, T, quality, date, lon_key, doy`

### [4] Climatology loading
- 파일명에서 `lon360` 추출
- `Depth(M):` + `DOY` 헤더 이후 각 행: `[doy] [Ts...] [Ns...] [Ws...]`
  - `T_clim` (mean), `n_clim` (sample count), `window_used` (DOY ± window)
- 결측 마커(`T < -9`)는 drop

### [5] Meridional weighted average (±5° band)
- 윈도우: 마지막 관측일 기준 직전 **15일**
- ±5°에 들어오는 위도(보통 -5°, -2°, 0°, +2°, +5°) 5라인을 trapezoidal weight로 평균:

  | Lat | Boundaries | Weight |
  |---|---|---|
  | -5° | [-5, -3.5] | 0.150 |
  | -2° | [-3.5, -1] | 0.250 |
  |  0° | [-1, +1]   | 0.200 |
  | +2° | [+1, +3.5] | 0.250 |
  | +5° | [+3.5, +5] | 0.150 |

- 셀별로 결측 위도가 있으면 가중치를 자동 재정규화 (`w_sum`으로 나눔)

### [6] Anomaly attachment (fallback chain)
각 `(lon_key, depth, doy)` 셀에 대해 climatology를 다음 순으로 부여:
1. **Exact match** — `(lon, depth, doy)` 정확 매칭
2. **Depth interp** — 같은 `(lon, doy)`의 다른 depth로 선형 보간
3. **Lon interp** — 같은 `(depth, doy)`의 가장 가까운 lon에서 가져옴
4. **In-period mean** — 마지막 fallback (15일 윈도우 자체 평균)

진단 출력에서 `exact / +depth_interp / +lon_interp / +inperiod_fallback` 분포 표시.

### [7] Section interpolation
- 격자: lon `np.arange(130, 271, 2.0)` × depth `np.arange(0, 351, 5.0)`
- `scipy.interpolate.griddata`로 linear → 빈 영역은 nearest fill
- 관측점에서 lon > 15° 또는 depth > 40 m 떨어진 지점은 NaN 마스크 (far-extrapolation 방지)
- 어떤 날 데이터가 비면 직전 날짜 section을 carry, tag `(carry)`

### [8] Plotting (per-day, two kinds)
각 날짜에 대해 두 PNG 생성:
- `pac_5_anomaly_YYYYMMDD.png` — anomaly contour fill + isotherm overlay
- `pac_5_absolute_YYYYMMDD.png` — absolute T contour fill + isotherm overlay

#### Color scales
- **Anomaly**: ±5°C, `RdBu_r`, CPC 표준 비균등 레벨 `[-5,-4,-3,-2,-1,-0.5,0,0.5,1,2,3,4,5]`
- **Absolute**: `turbo`, 2°C 간격 `[14,16,18,20,22,24,26,28,30]`
  - **<14°C는 단일 navy(`#0a1a3d`)** (set_under) — thermocline 구조 강조

#### Isotherms (양쪽 PNG 공통)
- 18, 20, 22, 24, 26, 28, 30°C 라벨링
- **20°C** 굵은 검정 (thermocline)
- **28°C** 굵은 빨강(anomaly) 또는 흰색(absolute) (warm-pool 경계)

#### Niño box overlay (anomaly PNG only)
플롯 상단(0–7 m) 띠 안에 Niño 4 / 3.4 / 3 / 1+2 box 라벨

#### X축
10° major tick, 자동으로 `°E`, `180°`, `°W` 라벨

### [9] CSV + zip
- `pac_5_data.csv`: `[date, longitude, lon_label, depth, doy, T_observed, T_climatology, anomaly, n_lats, mean_lat_used]`
  - 윈도우 외 날짜도 포함 (band 전체 행) — anomaly 계산이 day의 doy를 쓰므로 모든 raw 행이 들어감
- `pac_5_plots.zip`: 30개 PNG (15일 × 2종) + CSV, ZIP_DEFLATED level 9

---

## 5. Outputs

```
Output/Subsurface/
├── pac_5_anomaly_YYYYMMDD.png   × 15  (window 마지막 15일)
├── pac_5_absolute_YYYYMMDD.png  × 15
├── pac_5_data.csv               (전체 raw row, 보통 ~16k rows)
└── pac_5_plots.zip              (PNG×30 + CSV)
```

---

## 6. Run Procedure

의존성:
```
numpy pandas matplotlib scipy
```

로컬 실행 (Windows PowerShell):
```powershell
$env:PYTHONUTF8 = "1"
python "subsurface/run_subsurface.py"
```

`PYTHONUTF8=1`은 한글/유니코드(°, →, ✓) 콘솔 출력 위해 필요.

---

## 7. Diagnostics

스크립트 종료 직전 두 표를 stdout에 출력:
1. **Subsurface (50–200 m) anomaly 최대값** — Kelvin wave 동진 추적
2. **Surface (0–30 m) max T + 28°C 동서폭** — warm pool 경계 모니터링

---

## 8. Design Decisions & Tradeoffs

| Decision | Rationale | Tradeoff |
|---|---|---|
| 단일 ±5° 밴드 출력 | CPC standard와 직접 비교 | 다른 위도밴드 분석 필요 시 코드 추가 필요 |
| Trapezoidal 위도 가중 | 부이 라인이 위도상 비균등(-5,-2,0,+2,+5) | cosine area weight 무시 — ±5°에서는 무시 가능 |
| 4단계 climatology fallback | 관측 depth/lon이 climatology와 100% 매칭되지 않음 | lon-interp 단계에서 멀리 있는 lon이 잡히면 부정확 |
| Far-extrapolation 마스킹 (15° lon, 40 m depth) | 부이 사이 빈 공간을 nearest로 채워 시각적 oversmoothing 방지 | 부이 sparse 구간은 흰색으로 남음 |
| `(carry)` 태그 | 데이터 누락일을 직전 day로 채워 PNG 연속성 유지 | stale day가 보이지 않게 라벨 필요 |
| `Output/Subsurface/`로 분리 | SST 파이프라인과 출력 충돌 방지 | 두 디렉터리를 별도 관리 |
| `subsurface/pmel_data/`에 추출 | Input은 read-only로 유지, 코드 폴더에 작업 디렉터리 격리 | 추출 결과를 매 실행마다 재사용 (gunzip 멱등) |

---

## 9. Known Limitations & Future Work

1. **±5° 단일 밴드**: notebook에는 더 많은 밴드가 정의돼 있었음. 필요 시 `PAC_BANDS`에 추가.
2. **Window 길이 고정 (15일)**: `DAYS_BACK` 상수로만 변경 가능. CLI 인자로 노출 가능.
3. **추출 디렉터리 누적**: `subsurface/pmel_data/`에 이전 실행의 ASCII가 남음. 새 raw로 갱신할 때는 폴더 비우거나 자동 정리 로직 추가 필요.
4. **`carry` 태그 시각화**: 현재 title에만 표시. 더 가시적인 마커(투명도 등) 필요.
5. **Climatology 보간 정확도**: lon-interp 단계는 가장 가까운 lon만 보고 weighting 없음. 두 lon 사이 거리 가중 보간으로 개선 가능.
