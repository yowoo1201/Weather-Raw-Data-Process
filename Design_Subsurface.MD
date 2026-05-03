# Subsurface Daily Pipeline — Design Document

TAO/TRITON Pacific + **RAMA Indian Ocean** subsurface daily section pipeline.
Roman ASCII raw data를 파싱해 ±5° 위도밴드 daily section을 만들고,
per-longitude DOY climatology와 비교한 anomaly·absolute PNG와 zip을 만든다.

원본 노트북: `enso_buoy_doy_climatology.ipynb` (Colab) → 로컬 실행본
[subsurface/run_subsurface.py](subsurface/run_subsurface.py).

두 ocean basin은 동일한 processing flow를 공유하지만 입력 데이터, 격자, 박스 정의,
진단 지표가 다르다. Basin은 `--basin pacific` / `--basin indian` / `--basin both`
CLI 인자로 선택한다 (default: `both`).

---

## 1. Goal & Scope

### Goal — Pacific (ENSO)
- PMEL TAO/TRITON 부이 ASCII에서 적도 Pacific (±5°) 일별 cross-section 산출
- per-longitude DOY climatology와 비교해 anomaly section 산출
- CPC GODAS 스타일 anomaly PNG 및 absolute temperature PNG 생성 (15일치)
- Kelvin wave 동진 / warm pool 경계 / thermocline 깊이 추적

### Goal — Indian Ocean (ISM/IOD) [신규]
- PMEL RAMA 부이 ASCII에서 적도 Indian Ocean (±5°) 일별 cross-section 산출
- 동일한 climatology-anomaly framework 적용
- **IOD box-mean SST anomaly** (WTIO, SETIO) 및 DMI 일별 추적
- **벵골만/아라비아해 thermocline** 추적 (ISM onset 동력 평가)
- 적도 인도양 thermocline tilt → Bjerknes feedback 진단

### Out of scope
- ENSO 지표 산출 (rSSTA, ONI 등) — `sst/` 파이프라인 담당
- IOD index 자체의 공식 산출 (NOAA/BoM 발표값) — 이 파이프라인은 box-mean anomaly만
- ±5° 외 위도밴드 (notebook의 다른 밴드는 비활성화)
- SLA, OLR, U850 같은 다른 동적 변수
- 부이 climatology builder (미리 만들어진 tarball 사용)
- **EQUINOO U850 산출** (부이는 SST/subsurface T만 측정. 적도 zonal wind는 reanalysis 별도 파이프라인)

---

## 2. Inputs

| File | Source | Basin | Role |
|---|---|---|---|
| `Input/data.tar` | PMEL DDS TAO/TRITON | Pacific | Pacific daily raw buoy (±15일) |
| `Input/data_p_clim_processed.tar.gz` | climatology builder 출력 | Pacific | Pacific per-longitude DOY climatology |
| **`Input/data_rama.tar`** | **PMEL DDS RAMA** | **Indian** | **Indian daily raw buoy (±15일)** |
| **`Input/data_rama_clim_processed.tar.gz`** | **climatology builder 출력** | **Indian** | **Indian per-longitude DOY climatology** |

### Raw data sources
- PMEL TAO/TRITON: <https://www.pmel.noaa.gov/tao/data_deliv/deliv-nojava-all.html> → `data.tar`
- **PMEL RAMA**: <https://www.pmel.noaa.gov/tao/data_deliv/deliv-nojava-rama.html> → `data_rama.tar`
- 이전 이름 `data_p.tar`는 더 이상 사용하지 않음.

### Tarball 구조 (참고)
- `data.tar` 안: `t<lat><n|s><lon><e|w>_dy.ascii.gz` (예: `t5n140w_dy.ascii.gz`)
- `data_rama.tar` 안: 동일한 명명 규칙 (예: `t0n80_5e_dy.ascii.gz`, `t8s67e_dy.ascii.gz`)
  - **주의**: RAMA는 일부 부이가 소수점 경도(80.5°E, 67°E 등)
  - 파일명 파서는 `(\d+)_?(\d*)([ew])` 패턴으로 정수+소수부 분리 처리
- `data_p_clim_processed.tar.gz` 안: `t<lon><e|w>_clim.ascii.gz`
- `data_rama_clim_processed.tar.gz` 안: 동일 패턴 (`t80_5e_clim.ascii.gz` 등)

압축 해제는 스크립트가 자동 처리한다 (tar → 디렉터리, `.gz` → 평문 ASCII).

### RAMA 부이 분포 (참고, 가용 부이는 시기별 변동)
적도 라인 (`±5°` 밴드 핵심):
- **0°N**: 67°E, 80.5°E, 90°E
- **±2°**: 일부 시기만 운영 (sparse)
- **±5°**: 67°E, 80.5°E, 95°E

밴드 외부지만 ISM 진단용:
- **벵골만 (BoB)**: 12°N 90°E, 15°N 90°E (BD08~BD14 시리즈)
- **아라비아해**: 8°N 67°E, 15°N 65°E
- **남인도양**: 8°S 67°E, 8°S 80.5°E, 8°S 95°E, 16°S 80.5°E

Pacific 대비 RAMA는 **부이 sparse도가 훨씬 심함** — far-extrapolation 마스크 임계
완화 필요 (§4 참조).

---

## 3. Directory Layout

```
Code/
├── Input/
│   ├── data.tar                              ← Pacific raw
│   ├── data_p_clim_processed.tar.gz          ← Pacific clim
│   ├── data_rama.tar                         ← Indian raw [신규]
│   └── data_rama_clim_processed.tar.gz       ← Indian clim [신규]
├── Output/
│   └── Subsurface/
│       ├── Pacific/                          ← 기존 출력 이전 [경로 변경]
│       │   ├── pac_5_anomaly_YYYYMMDD.png
│       │   ├── pac_5_absolute_YYYYMMDD.png
│       │   ├── pac_5_data.csv
│       │   └── pac_5_plots.zip
│       └── Indian/                           ← 신규
│           ├── ind_5_anomaly_YYYYMMDD.png
│           ├── ind_5_absolute_YYYYMMDD.png
│           ├── ind_5_data.csv
│           ├── ind_iod_boxes.csv             ← WTIO/SETIO/DMI 일별
│           ├── ind_iod_timeseries.png        ← DMI 15일 추이
│           └── ind_5_plots.zip
├── subsurface/
│   ├── run_subsurface.py                     ← --basin 인자 추가
│   ├── basin_config.py                       ← Pacific/Indian 격자·박스 정의 [신규]
│   └── pmel_data/
│       ├── pacific_now/  pacific_clim/
│       └── indian_now/  indian_clim/         ← 신규
└── Design_Subsurface.MD
```

**Breaking change**: 기존 `Output/Subsurface/pac_*` 산출물이 `Output/Subsurface/Pacific/`로
이동. 기존 사용자 스크립트가 경로를 가정하면 마이그레이션 필요.

---

## 4. Pipeline Stages

```
Input/data.tar          ──► [1] extract  ──► [3] PMEL ASCII parse ──► [5] ±5° band weighted mean
Input/data_rama.tar     ──► [1] extract  ──► [3] PMEL ASCII parse ──► [5] ±5° band weighted mean
                                                                                 │
Input/*pac_clim*.tar.gz ► [1] extract ► [4] climatology load ─► [6] anomaly attach
Input/*rama_clim*.tar.gz► [1] extract ► [4] climatology load ─► [6] anomaly attach
                                                                                 │
                                            [7] section grid ◄────────────────────┤
                                                                                 │
                                            [8] per-day PNG  ◄────────────────────┤
                                                                                 │
                                            [9] CSV + zip    ◄────────────────────┤
                                                                                 │
                              [10] IOD box aggregation (Indian only) ◄─────────────┘
```

### [1] Extraction
- Pacific: `Input/data.tar` → `subsurface/pmel_data/pacific_now/`
- Pacific clim: `Input/data_p_clim_processed.tar.gz` → `subsurface/pmel_data/pacific_clim/`
- **Indian**: `Input/data_rama.tar` → `subsurface/pmel_data/indian_now/`
- **Indian clim**: `Input/data_rama_clim_processed.tar.gz` → `subsurface/pmel_data/indian_clim/`
- 입력 tarball 누락 시 해당 basin만 skip하고 stderr에 경고 (다른 basin은 계속).

### [3] PMEL DDS ASCII parsing (raw)
- 두 basin 동일 로직. 파일명에서 `(lat, lon360)` 추출.
- **RAMA용 확장**: 소수점 경도 처리 (`80_5e` → 80.5°E). lat/lon 정규식을
  `(\d+)(?:_(\d+))?([nsew])` 패턴으로 일반화.
- 다중 블록(블록당 다른 depth set) 파싱.
- Quality flag ≤ 3, T ∈ (-9, 40) 검증.
- 출력: `time, latitude, longitude, depth, T, quality, date, lon_key, doy, basin`

### [4] Climatology loading
- 두 basin 동일 로직. `[doy] [Ts...] [Ns...] [Ws...]` 형식.
- 결측 마커(`T < -9`)는 drop.

### [5] Meridional weighted average (±5° band)
- 윈도우: 마지막 관측일 기준 직전 15일 (두 basin 공통).
- Pacific 위도 weight (기존):

  | Lat | Boundaries | Weight |
  |---|---|---|
  | -5° | [-5, -3.5] | 0.150 |
  | -2° | [-3.5, -1] | 0.250 |
  |  0° | [-1, +1]   | 0.200 |
  | +2° | [+1, +3.5] | 0.250 |
  | +5° | [+3.5, +5] | 0.150 |

- **Indian 위도 weight** (RAMA는 ±2° 부이가 거의 없음):

  | Lat | Boundaries | Weight |
  |---|---|---|
  | -5° | [-5, -2.5] | 0.250 |
  |  0° | [-2.5, +2.5] | 0.500 |
  | +5° | [+2.5, +5] | 0.250 |

  ±2° 부이가 시기별로 가용하면 자동 fallback해서 Pacific식 5점 weight 사용 (코드에서
  available latitudes 검사 후 적절한 weight set 선택).

- 셀별로 결측 위도가 있으면 가중치를 자동 재정규화 (`w_sum`으로 나눔).

### [6] Anomaly attachment (fallback chain)
각 `(lon_key, depth, doy)` 셀에 대해 climatology를 다음 순으로 부여:
1. **Exact match** — `(lon, depth, doy)` 정확 매칭
2. **Depth interp** — 같은 `(lon, doy)`의 다른 depth로 선형 보간
3. **Lon interp** — 같은 `(depth, doy)`의 가장 가까운 lon에서 가져옴
4. **In-period mean** — 마지막 fallback (15일 윈도우 자체 평균)

진단 출력에서 basin별 분포 표시.

### [7] Section interpolation
Basin별 격자 (basin_config.py에 정의):

| Param | Pacific | Indian |
|---|---|---|
| Longitude range | `np.arange(130, 271, 2.0)` | `np.arange(40, 101, 2.0)` |
| Depth range | `np.arange(0, 351, 5.0)` | `np.arange(0, 351, 5.0)` |
| Far-extrapolation lon mask | 15° | **20°** (RAMA sparse) |
| Far-extrapolation depth mask | 40 m | 40 m |

- `scipy.interpolate.griddata` linear → 빈 영역은 nearest fill.
- 마스크 임계 초과 지점은 NaN.
- 결측일은 직전 day section을 carry, tag `(carry)`.

### [8] Plotting (per-day, two kinds)
각 날짜에 대해 두 PNG 생성. 두 basin 동일한 colormap·isotherm 규칙.

#### Color scales
- **Anomaly**: ±5°C, `RdBu_r`, CPC 표준 비균등 레벨 `[-5,-4,-3,-2,-1,-0.5,0,0.5,1,2,3,4,5]`
- **Absolute**: `turbo`, 2°C 간격 `[14,16,18,20,22,24,26,28,30]`
  - <14°C는 단일 navy(`#0a1a3d`) (set_under) — thermocline 구조 강조

#### Isotherms (양쪽 basin 공통)
- 18, 20, 22, 24, 26, 28, 30°C 라벨링
- **20°C** 굵은 검정 (thermocline)
- **28°C** 굵은 빨강(anomaly) / 흰색(absolute) (warm-pool 경계)

#### Box overlay (anomaly PNG only)
플롯 상단(0–7 m) 띠 안에 box 라벨:
- **Pacific**: Niño 4 / 3.4 / 3 / 1+2
- **Indian**: **WTIO [50–70°E] / Central [70–90°E] / SETIO [90–110°E의 적도 부분]**

#### X축
10° major tick, 자동으로 `°E`, `180°`, `°W` 라벨.
- Pacific: 130°E ~ 90°W
- Indian: **40°E ~ 100°E** (전부 °E)

### [9] CSV + zip
- Basin별로 `<prefix>_data.csv` + `<prefix>_plots.zip` 생성.
- Prefix: Pacific=`pac_5`, Indian=`ind_5`.
- ZIP: 30 PNG (15일 × 2종) + CSV, ZIP_DEFLATED level 9.

### [10] IOD box aggregation (Indian only) [신규]
±5° 밴드 산출물과 별개로, raw row 데이터(밴드화 전)에서 IOD-relevant box 평균을 따로 산출.

**Box 정의** (Saji et al. 1999):
- **WTIO**: lon [50°E, 70°E], lat [10°S, 10°N], depth=surface (0–10 m)
- **SETIO**: lon [90°E, 110°E], lat [10°S, 0°], depth=surface (0–10 m)

**산출 대상**: 각 box, 각 날짜에 대해 SST anomaly box-mean.

**DMI proxy** = WTIO mean SSTA − SETIO mean SSTA.

**한계 고지**:
- RAMA 부이는 box를 sparse하게 sampling. 정식 IOD index(NOAA/BoM)는 grid SST 사용.
- 이 산출물은 **부이 기반 proxy**로 단기 변화 모니터링용. 절대값보다 추세에 의미.
- Box당 부이 수가 적은 날(<2)은 NaN 마스크.

**출력**:
- `Output/Subsurface/Indian/ind_iod_boxes.csv`:
  `[date, box, n_buoys, ssta_mean, ssta_std, lats_used, lons_used]`
- `Output/Subsurface/Indian/ind_iod_timeseries.png`:
  15일 WTIO/SETIO/DMI line plot. ±0.4°C / ±1.0°C threshold 가로선 overlay.

---

## 5. Outputs

```
Output/Subsurface/
├── Pacific/
│   ├── pac_5_anomaly_YYYYMMDD.png   × 15
│   ├── pac_5_absolute_YYYYMMDD.png  × 15
│   ├── pac_5_data.csv
│   └── pac_5_plots.zip
└── Indian/
    ├── ind_5_anomaly_YYYYMMDD.png   × 15
    ├── ind_5_absolute_YYYYMMDD.png  × 15
    ├── ind_5_data.csv
    ├── ind_iod_boxes.csv            (WTIO/SETIO/DMI 15일 일별)
    ├── ind_iod_timeseries.png       (DMI 추이 + threshold lines)
    └── ind_5_plots.zip              (PNG×30 + CSV + IOD CSV/PNG)
```

---

## 6. Run Procedure

의존성 (변경 없음):
```
numpy pandas matplotlib scipy
```

로컬 실행 (Windows PowerShell):
```powershell
$env:PYTHONUTF8 = "1"

# 두 basin 모두 (default)
python "subsurface/run_subsurface.py"

# Pacific만
python "subsurface/run_subsurface.py" --basin pacific

# Indian만
python "subsurface/run_subsurface.py" --basin indian
```

`PYTHONUTF8=1`은 한글/유니코드(°, →, ✓) 콘솔 출력 위해 필요.

---

## 7. Diagnostics

스크립트 종료 직전 basin별로 표 출력.

### Pacific (기존)
1. **Subsurface (50–200 m) anomaly 최대값** — Kelvin wave 동진 추적
2. **Surface (0–30 m) max T + 28°C 동서폭** — warm pool 경계 모니터링

### Indian [신규]
1. **WTIO / SETIO / DMI proxy** — 최근 5일 / 15일 평균. ±0.4°C 임계 도달 여부.
2. **적도 thermocline tilt** — 20°C isotherm depth at 60°E vs 90°E 차이.
   양수면 서쪽 깊음(평년) / 작아지거나 음수면 +IOD 신호 (동쪽 thermocline 얕아짐).
3. **벵골만 (12°N–15°N, 90°E) 표층 30 m 평균 T** — pre-monsoon TC 발달 환경 + ISM moisture supply.
4. **아라비아해 (8°N–15°N, 65°E–67°E) 표층 30 m 평균 T** — 케랄라 onset moisture source.

---

## 8. Design Decisions & Tradeoffs

| Decision | Rationale | Tradeoff |
|---|---|---|
| 두 basin 단일 스크립트 통합 | parsing/plotting 코드 재사용, climatology fallback 동일 로직 | basin-specific 분기가 곳곳에 들어가 readability 저하 |
| Basin별 출력 디렉터리 분리 | 같은 prefix(pac_5 vs ind_5)지만 충돌 방지 | 기존 Pacific 사용자에게 path breaking change |
| Indian 3점 위도 weight (±5°, 0°만) | RAMA ±2° 부이가 시기 의존적으로 결측 | 0° 부이만 있는 시기는 밴드 평균이 strict equator 편향 |
| Indian far-extrapolation 마스크 20° | RAMA sparse, 15°로는 빈 흰색 너무 많음 | over-smoothing 위험 (40°E ~ 100°E에 부이 ~9개) |
| IOD proxy 별도 산출 | ±5° 밴드 산출물로는 IOD 추적 불가 (위도 box 다름) | 부이 sparse로 box-mean 신뢰도 낮음 |
| EQUINOO 미포함 | 부이가 U850 측정 안 함 | 적도 zonal wind는 reanalysis 기반 별도 파이프라인 필요 |
| 단일 ±5° 밴드 출력 | CPC standard 비교, 두 basin 공통 표준 | 다른 위도밴드 분석 시 코드 추가 필요 |
| 4단계 climatology fallback | 관측 depth/lon이 climatology와 100% 매칭 안 됨 | lon-interp 단계에서 멀리 있는 lon이 잡히면 부정확 (Indian이 더 심함) |
| `(carry)` 태그 | 누락일을 직전 day로 채워 PNG 연속성 유지 | stale day 라벨 필요 |

---

## 9. Known Limitations & Future Work

1. **Pacific 단일 ±5° 밴드 / Indian 단일 ±5° 밴드**: notebook에 더 많은 밴드. 필요 시
   `BANDS` config에 추가.
2. **Window 길이 고정 (15일)**: `DAYS_BACK` 상수. CLI 인자 노출 가능.
3. **추출 디렉터리 누적**: `subsurface/pmel_data/`에 이전 실행 ASCII가 남음. 새 raw로
   갱신할 때 폴더 비우거나 자동 정리 로직 필요.
4. **`carry` 태그 시각화**: title에만 표시. 더 가시적인 마커 필요.
5. **Climatology lon-interp 정확도**: 가장 가까운 lon만 보고 weighting 없음. 두 lon
   사이 거리 가중 보간으로 개선 가능.
6. **IOD proxy 신뢰도**: RAMA 부이가 box를 sparse 샘플링. 정식 NOAA/BoM IOD index와
   bias 가능. 비교 검증 단계 필요.
7. **벵골만/아라비아해 진단 부이 의존성**: 12°N/15°N 90°E (BoB), 8°N/15°N 65°E (AS)
   부이가 결측 시 진단 NaN. 필요 시 reanalysis (OISST, HYCOM) fallback 추가 검토.
8. **EQUINOO 포함 미정**: ISM 진단의 핵심 변수지만 부이로 산출 불가. 별도
   `equatorial_wind/` 파이프라인 (ERA5 U850) 신설 필요.
9. **±5° band의 ISM 직접 관련성 약함**: ISM 본 활동은 위도 8°N–25°N. 적도 ±5°는
   IOD/MJO/Bjerknes 동력만 추적 가능. ISM 강수 직접 진단은 GPCP/IMD 격자 강수 별도
   파이프라인 필요.

---

## 10. ISM/IOD 분석 활용 가이드 [신규]

이 파이프라인 산출물로 답할 수 있는 / 없는 것:

### 답할 수 있는 것
- 적도 인도양 thermocline 일별 변화 → Bjerknes feedback 진행 여부
- WTIO/SETIO box-mean SSTA 추세 → IOD 발달 초기 신호
- 20°C isotherm tilt 변화 → +IOD vs −IOD 방향성
- 벵골만/아라비아해 표층 T → pre-monsoon TC 환경 + onset moisture
- 5일/15일 평균으로 단기 perturbation (TC cold wake 등) 시각화

### 답할 수 없는 것 (다른 data source 필요)
- MJO RMM phase/amplitude → BoM/CPC RMM index 직접 fetch
- ISM 강수 (케랄라 onset, JJAS 누적) → IMD / GPCP 격자
- EQUINOO U850 → ERA5 / NCEP reanalysis
- 정식 DMI 값 → NOAA PSL / BoM 발표값
- TC 진로/강도 → JTWC / IMD bulletin

이 파이프라인은 **인도양 subsurface state**의 일별 모니터링 도구. 시즌 전망은
이 산출물 + 외부 index들을 조합해서 진단해야 함.
