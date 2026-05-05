# SST/rSSTA Daily Pipeline — Design Document

ENSO 일별 모니터링 파이프라인 (`ElNinoRSSTDailyCalculation.ipynb`) 설계 문서.
OISST .nc 파일 + CPC 주간 SSTA 참조값으로부터 box-averaged SST, anomaly,
relative SSTA(rSSTA), 동서 경사를 계산하고, 보정·검증·시각화·보고서까지
하나의 파이프라인으로 묶는다.

---

## 1. Goal & Scope

### Goal
- OISST v2.1 AVHRR 일별 .nc 파일에서 4개 Niño 박스의 SST 시계열을 산출
- CPC 1991–2020 climatology, CPC 주간 SSTA를 참조해 일별 anomaly와 rSSTA를 계산
- ENSO 상태(El Niño / La Niña / Neutral, CP vs EP)를 raw rSSTA로 일별 판정
- 무역풍 forcing 대리지표인 동서 SST 경사와 그 climatological 편차를 추적

### Out of scope
- ERSSTv5 기반 RONI K-factor (variance correction, K≈1.5)는 **적용하지 않음**.
  OISST의 native variance가 ERSST와 달라, K를 그대로 쓰면 부정확. OISST 전용
  K 추정 또는 OISST→ERSST bias 보정이 선행되어야 함 (별도 작업).
- ONI 3-month running mean 기반 공식 ENSO 선언과는 직접 비교하지 않음.
- 동적 변수(SLA, OLR, U200 등) 분석은 포함하지 않음.

---

## 2. Inputs

모든 입력은 프로젝트 루트의 `Input/` 폴더에 위치한다:

| File | Source | Role | Fetch |
|---|---|---|---|
| `Input/oisst_data/oisst-avhrr-v02r01.YYYYMMDD.nc` | NOAA OISST v2.1 | 일별 0.25° SST grid (preliminary 파일은 `_preliminary` 접미사) | 수동 |
| `Input/nino_clim_daily_1991-2020.csv` | setup 단계 산출 | 365일 × 4박스 climatology mean | 수동 (1회, static) |
| `Input/wksst9120.for.txt` | CPC `wksst9120.for` | 주간 절대 SST + 절대 SSTA (4박스) | **자동** (run_pipeline 시작 시) |
| `Input/rel_wksst9120.txt` | CPC relative SSTA | 주간 relative SSTA (4박스, 열대평균 차감 기준) | **자동** (run_pipeline 시작 시) |

### CPC 자동 fetch
`run_pipeline.py`가 시작 시점(stage [0])에 다음 두 URL에서 매번 새로 받아 `Input/`에 덮어쓴다:
- <https://cpc.ncep.noaa.gov/data/indices/wksst9120.for> → `Input/wksst9120.for.txt`
- <https://cpc.ncep.noaa.gov/data/indices/rel_wksst9120.txt> → `Input/rel_wksst9120.txt`

네트워크 실패 시 기존 로컬 파일이 있으면 그대로 사용 (warning), 둘 다 없으면 종료.
`urllib.request` 표준 라이브러리만 사용 — 추가 의존성 없음.

### Niño box 정의 (Pacific, lon = 0–360)

| Box | Lat | Lon |
|---|---|---|
| Niño 1+2 | -10° to 0° | 270° to 280° |
| Niño 3   | -5° to 5°  | 210° to 270° |
| Niño 3.4 | -5° to 5°  | 190° to 240° |
| Niño 4   | -5° to 5°  | 160° to 210° |

---

## 3. Pipeline Stages

전체 흐름:

```
[0]  CPC fetch (urllib) ──► Input/wksst9120.for.txt + rel_wksst9120.txt
[0b] OISST fetch (urllib) ─► Input/oisst_data/oisst-avhrr-v02r01.YYYYMMDD.nc
                                          │
.nc files ──► [1] box-mean SST ──► [2] anomaly (vs clim) ──► [3] CPC bias 보정
                                                                    │
   CPC abs+rel SSTA ──► [4] tropical mean 역산 + 추세 외삽 ───────► [5] rSSTA
                                                                    │
                                            [6] gradient anomaly ◄──┤
                                                                    │
                          [7] 주별 집계 / [8] 플롯 / [9] 보고서 ◄──┘
```

### [0] CPC 자동 fetch
- 두 URL을 `urllib.request.urlopen`(timeout=20s)으로 받아 `Input/`에 덮어쓴다.
- 네트워크 실패 + 로컬 파일 존재: 경고 후 진행. 네트워크 실패 + 로컬 파일 없음: 즉시 종료.
- CPC 주간 bulletin은 매주 수요일경 갱신 (week_center가 일요일 기준 +3일).
  매 실행마다 fresh로 받아 stale 데이터 위험 제거.

### [0b] OISST 자동 fetch [v1.001]
- [sst/fetch_oisst.py](sst/fetch_oisst.py)의 `fetch_months()` 호출 — 이번 달 + 지난 달
  NCEI 디렉터리 walk:
  `https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr/<YYYYMM>/`
- 날짜별 best 버전 결정 (final 우선, 없으면 preliminary).
- **Preliminary는 final로 자동 overwrite** — 로컬에 prelim만 있고 remote에 final 있으면
  final 받고 prelim 삭제. 로컬에 final + prelim 둘 다 있으면 prelim 삭제 (final 보존).
- 결과: 날짜당 정확히 1개 파일 → SST 분석 stage [1]에서 중복 행 없이 처리.
- 네트워크 실패는 non-fatal — 기존 로컬 파일로 계속 진행.
- 수동 호출도 가능: `python sst/fetch_oisst.py --months 202604,202605`

### [1] Box-mean SST (`xarray`)
- 각 .nc 파일에서 `sst.squeeze()`로 단일 timestep 추출 (zlev 차원 제거)
- 박스별 **area-weighted mean** 적용:
  - `weights = cos(deg2rad(lat))`
  - `sst.sel(lat=slice, lon=slice).weighted(w).mean(('lat','lon'))`
- preliminary 파일은 파일명의 `prelim` 키워드로 플래그 (시각화 시 빈 마커)

### [2] Climatology anomaly
- `(month, day)`로 클라이마톨로지 join → `nino{N}_anom = nino{N} - nino{N}_clim`
- 윤년 02-29는 클라이마톨로지에 없으면 NaN — 현재 로직은 join 결과를 그대로
  사용 (데이터 누락 시 후속 단계에서 NaN 전파).

### [3] CPC 주간 bias 오프셋
- CPC 주간 절대 SSTA(`wksst9120.for.txt`)와 OISST anomaly의 평균을 비교해
  관측 기간 내 박스별 mean bias를 산출.
- 각 CPC 주간 중심일 ±3일 윈도우에서 OISST anomaly 평균 vs CPC SSTA의 차이를
  모아 평균 → `offsets[r]`. 4일 미만이면 해당 주는 스킵.
- 보정값: `nino{N}_anom_corrected = nino{N}_anom - offsets[r]`
- **목적**: OISST와 CPC 사이의 공정한 reference 정렬 (same climatology base
  사용해도 처리 차이로 미세한 systematic offset 존재).

### [4] Tropical mean (20°S–20°N) 역산 + 추세 외삽
CPC가 절대 SSTA와 relative SSTA를 모두 제공하므로:

```
tropical_mean ≈ mean_over_4_boxes(absolute_SSTA - relative_SSTA)
```

이를 주간값으로 얻고, OISST 일별 격자에 매핑하기 위해 다음 두 단계:

1. **관측 구간 내 (`d ≤ last_obs_date`)**: 양옆 CPC 주간값으로 선형 시간 보간.
2. **외삽 구간 (`d > last_obs_date`)**: 최근 N=8주의 `(week_center, tropmean)`에
   `scipy.stats.linregress`로 선형 fit. 마지막 관측값 + slope×days_after로
   외삽. R²과 trend(°C/주) 보고.

> **설계 의도**: CPC 주간값은 보통 7–10일 지연되므로 단순 forward-fill하면
> 최근 며칠 rSSTA가 stale tropical mean으로 왜곡됨. 추세 외삽이 그 갭을 메움.
> 단 외삽 범위는 일반적으로 7–14일 이내로만 신뢰.

### [5] rSSTA (relative SSTA)
```
rSSTA[r,d] = anom_corrected[r,d] - tropical_mean_ssta[d]
```
- **K factor 미적용 (K=1)**. RONI의 공식 정의 `RONI = (anom - tropmean) / K_r`
  은 박스별 분산비를 곱하지만, 그 K는 ERSSTv5에서 추정된 값이라 OISST에 직접
  쓰면 부정확. 따라서 본 파이프라인은 **raw rSSTA**(K=1)만 산출하며, ONI
  ±0.5°C threshold와 직접 비교하지 않음.
- 보고서에서는 raw scale 전용 임계값(±0.33, ±0.65, ±1.0)을 사용. 경험적으로
  ONI 임계의 1/1.5배 수준.

### [6] 동서 경사 (zonal gradient)
4개 페어:
- `grad_4_minus_12`  (Niño 4 − Niño 1+2): 동서 전체 폭
- `grad_4_minus_3`   (Niño 4 − Niño 3)
- `grad_34_minus_12` (Niño 3.4 − Niño 1+2)
- `grad_4_minus_34`  (Niño 4 − Niño 3.4)

각 경사에 대해 climatology 경사(`grad_*_clim`)와 anomaly(`grad_*_anom`) 산출.
**음의 anomaly = 평년보다 약한 동서 경사 = 무역풍 약화 가능 신호 (El Niño-like)**.

### [7] 주별 집계 (Sunday-start)
- `week_start = date - ((weekday+1) % 7)일`로 일요일 시작 주 정의
- `week_center = week_start + 3d` (CPC와 정렬)
- 7일 미만 부분주는 시각화에서 점선 + `*` 마커
- 주별 평균 컬럼: rSSTA, anom_corrected, raw SST, 경사 absolute / anomaly
- CPC relative SSTA(`rel`)과 `week_center` 기준 left-join → 동일 행에서 비교

### [8] 시각화
- **`nino_rssta_plot.png`** (4-panel, 박스별):
  - 일별 라인 (실선) + Final/Prelim 점 마커
  - 주간 평균 수평 막대 (완전주: 진한 실선, 부분주: 점선)
  - CPC 주간 relative SSTA: 검은 X 마커 (검증용)
  - 0선, ±0.5°C ONI 임계 (참고용 점선)
  - 마지막 일별 값 라벨
- **`nino_gradients_plot.png`** (2-panel):
  - 상단: 경사 절대값 vs 1991–2020 climatology
  - 하단: 경사 anomaly + 음수 영역 음영 (weaker → El Niño-like)

### [9] 보고서 (`nino_report.md`)
자동 생성 markdown 섹션:
- Verdict (raw rSSTA scale 분류)
- CP vs EP 패턴 판정 (Modoki criterion: Niño 3.4 > Niño 1+2 by >0.3°C)
- 박스별 최신값 + 7일 추세
- 동서 경사 해석
- 주별 요약 테이블
- 최근 CPC 공식 주간 rSSTA 비교
- CPC 대비 mean bias (검증)

---

## 4. Outputs

모든 산출물은 `Output/SST/` 아래에 작성된다:

```
Output/SST/
├── nino_rssta_daily.csv       # 일별 전체 (anomaly, anomaly_corrected, rSSTA, gradient들)
├── nino_rssta_weekly.csv      # 주별 평균 + CPC relative SSTA join
├── nino_rssta_plot.png        # 4-panel rSSTA 시계열
├── nino_gradients_plot.png    # 2-panel 동서 경사
├── nino_report.md             # markdown 보고서
└── nino_analysis.zip          # 위 5개 파일 묶음
```

### Directory layout (프로젝트 루트 기준)

```
Code/
├── Input/
│   ├── oisst_data/oisst-avhrr-v02r01.*.nc
│   ├── nino_clim_daily_1991-2020.csv
│   ├── wksst9120.for.txt
│   └── rel_wksst9120.txt
├── Output/
│   └── SST/                    ← 모든 SST 산출물
├── sst/
│   └── run_pipeline.py         ← 실행 스크립트
└── Design_SST.MD
```

스크립트는 자기 위치(`__file__`)를 기준으로 `Input/`과 `Output/SST/` 경로를 계산하므로 실행 위치와 무관하게 동작한다.

### `nino_rssta_daily.csv` 주요 컬럼
- `date`, `file`, `preliminary`
- `nino{12,3,34,4}` — box-mean SST
- `nino{N}_clim` — climatology
- `nino{N}_anom` — raw anomaly
- `nino{N}_anom_corrected` — CPC bias 보정 anomaly
- `tropical_mean_ssta` — 일별 열대평균 (보간/외삽)
- `nino{N}_rssta` — **최종 rSSTA (K=1)**
- `grad_*` — 경사 절대값 / climatology / anomaly

---

## 5. Key Formulas

```
# Area-weighted box mean
SST_box = Σᵢ wᵢ·SSTᵢ / Σᵢ wᵢ ,    wᵢ = cos(latᵢ)

# Anomaly chain
anom            = SST_box - clim(month, day)
anom_corrected  = anom - bias_offset_box        # CPC alignment

# Tropical mean (from CPC weekly)
tropmean_week ≈ mean_4boxes( CPC_abs_SSTA - CPC_rel_SSTA )

# rSSTA (K=1, no variance correction)
rSSTA = anom_corrected - tropmean_daily

# Zonal gradient anomaly
grad_anom = (SST_box_west - SST_box_east) - (clim_west - clim_east)
```

---

## 6. Classification Thresholds (raw rSSTA scale)

raw rSSTA는 ONI보다 분산이 작으므로 임계값을 줄임 (≈ ONI ÷ 1.5):

| rSSTA (°C) | Status |
|---|---|
| ≥ +1.00 | Very Strong Warm |
| ≥ +0.65 | Strong Warm |
| ≥ +0.33 | El Niño threshold (raw) |
| ≥ +0.20 | Warm-leaning |
| (-0.20, +0.20) | Neutral |
| ≤ -0.20 | Cool-leaning |
| ≤ -0.33 | La Niña threshold (raw) |
| ≤ -0.65 | Strong Cold |

**주의**: 공식 ENSO 선언은 ONI/RONI의 3개월 이동평균 + 5연속 시즌 기준.
본 표는 일별 *경향* 모니터링용이며 공식 진단을 대체하지 않음.

### CP/EP 분류 (Modoki criterion)
- `Niño3.4 > +0.3` AND `Niño3.4 - Niño1+2 > 0.3` → **CP-type (Modoki)**
- `Niño1+2 > +0.3` AND `Niño1+2 - Niño3.4 > 0.3` → **EP-type**

### 경사 anomaly 해석
| grad anomaly (4-1+2) | 해석 |
|---|---|
| < -0.5°C | 평년보다 경사 약함 → 무역풍 약화 가능 (El Niño-like) |
| > +0.5°C | 평년보다 경사 강함 → 무역풍 강화 (La Niña-like) |
| 그 외 | 평년 수준 |

---

## 7. Validation Strategy

**[3] 단계의 mean bias가 검증 지표 역할**:
- OISST anomaly의 주별 평균과 CPC 절대 SSTA의 차이를 박스별 평균
- 일반적으로 |bias| < 0.1°C 면 reference frame이 잘 정렬된 것
- bias가 크면 climatology 또는 OISST 처리에 issue 의심

**보고서에 항상 표시**되어 검증을 항상 가시화.

---

## 8. Design Decisions & Tradeoffs

| Decision | Rationale | Tradeoff |
|---|---|---|
| K=1 (no variance correction) | ERSSTv5 K를 OISST에 적용하면 부정확 | ONI 임계값과 직접 비교 불가 → 별도 raw-scale 임계 필요 |
| 추세 외삽 (linregress N=8주) | CPC 주간값 7–10일 지연을 메움 | 외삽 정확도는 trend stationarity에 의존; >2주 외삽은 신뢰도 하락 |
| Sunday-start week | CPC week_center가 수요일 → Sun-start로 정렬 | 표시 weekday 변환 시 혼동 가능 |
| 일요일 ≤ 4일 미만은 부분주 처리 | 평균 신뢰도 확보 | 가장 최근 주가 항상 부분주로 표시되는 경우가 잦음 |
| Area-weighted (cos lat) | 위도 폭 보정 | regular grid 가정 — 다른 grid 시 weighting 재정의 필요 |
| `preliminary` 플래그 시각적 구분 | NRT preliminary 파일은 사후 final로 갱신됨 | 같은 날짜 final/prelim 둘 다 있을 시 sort 순서 의존 |

---

## 9. Run Procedure (Local Windows)

의존성:
```
xarray netCDF4 scipy matplotlib pandas numpy
```

Colab notebook의 cell 1(`drive.mount`)과 cell 0 마지막의 `files.download`는
로컬에서 ImportError로 자동 우회됨.

로컬 실행 (프로젝트 루트 `Code/` 에서):
```powershell
$env:PYTHONUTF8 = "1"
python sst/run_pipeline.py
```
(Windows 콘솔 한글 출력 위해 UTF-8 모드 필요. 스크립트는 자기 경로 기준으로
`../Input/`을 읽고 `../Output/SST/`에 쓰므로 어느 cwd에서 실행해도 동작.)

---

## 10. Known Limitations & Future Work

1. **K factor 미적용**: ONI/RONI와 직접 비교 불가. 향후 OISST native K 추정
   (1991–2020 일별 분산 기반) 또는 OISST→ERSST bias 보정 필요.
2. **추세 외삽**: 8주 선형 fit은 ENSO 전환기에 systematic error 발생 가능.
   short-window EWMA 또는 Kalman filter로 개선 여지.
3. **윤년 (02-29)**: climatology에 02-29 없으면 NaN. 02-28과 03-01 평균 등으로
   채우는 옵션 추가 필요.
4. **부분주 가중**: 현재 `n_days < 7`은 단순 평균. 일별 가중치 적용으로 개선
   가능.
5. **Bias 단일값**: 박스별 단일 mean bias만 사용. 시간 의존(월별/계절별) bias
   확장 가능.
6. **단일 grid 가정**: OISST 0.25° regular grid 기준. 다른 dataset 추가 시
   `area_weighted_mean` 재구현 필요.
