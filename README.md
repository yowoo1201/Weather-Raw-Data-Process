# Weather-Raw-Data-Process

ENSO·IOD 일별 모니터링 파이프라인 — OISST 표면 SST(rSSTA, ENSO) + 적도 Pacific(TAO/TRITON) 및 적도 Indian Ocean(RAMA) 부이 subsurface 단면도.

ENSO/IOD daily monitoring pipelines — OISST surface SST (rSSTA, ENSO) + equatorial Pacific (TAO/TRITON) and equatorial Indian Ocean (RAMA) buoy subsurface cross-sections.

## v1.001 — Automation 추가
- **CPC 주간 bulletin 자동 fetch** (sst/run_pipeline 시작 시)
- **OISST `.nc` 자동 fetch** (`sst/fetch_oisst.py`, 매 실행 시 호출, prelim → final 자동 교체)
- **PMEL DDS 자동 fetch** (`subsurface/fetch_pmel.py`, Pacific TAO/TRITON + RAMA Indian
  form 폼 자동 제출 + tar 다운로드)
- 파이프라인 실패 시 **GUI 팝업** (`run_all.py`, tkinter messagebox)
- RAMA 소수점 경도 부이 누락 버그 수정 (form checkbox value 정규식)
- Pacific 그리드 130°E → 137°E + extrap_lon_mask 15° → 35° (서쪽 빈칸 제거)
- Subsurface absolute 컬러맵에 `>30 °C` over color 추가

---

## 구성 / Structure

```
Code/
├── Input/                                    # 원천 데이터
│   ├── oisst_data/*.nc                       # NOAA OISST v2.1 AVHRR 일별 .nc
│   ├── nino_clim_daily_1991-2020.csv         # static (committed)
│   ├── wksst9120.for.txt                     # CPC 주간 절대 SSTA (auto-fetch)
│   ├── rel_wksst9120.txt                     # CPC 주간 relative SSTA (auto-fetch)
│   ├── data.tar                              # PMEL TAO/TRITON Pacific buoy
│   ├── data_p_clim_processed.tar.gz          # Pacific per-lon DOY clim (static, committed)
│   ├── data_rama.tar                         # PMEL RAMA Indian buoy (Indian basin 시)
│   └── data_rama_clim_processed.tar.gz       # Indian per-lon DOY clim (Indian basin 시)
├── Output/                                   # 자동 생성 (gitignored, overwrite 매 실행)
│   ├── SST/                                  # nino_*.csv, *.png, nino_report.md, .zip
│   └── Subsurface/
│       ├── Pacific/                          # pac_5_*.png, pac_5_data.csv, pac_5_plots.zip
│       └── Indian/                           # ind_5_*.png, ind_5_data.csv, ind_iod_*, ind_5_plots.zip
├── sst/run_pipeline.py                       # SST/rSSTA pipeline
├── subsurface/
│   ├── run_subsurface.py                     # --basin pacific|indian|both
│   └── basin_config.py                       # Pacific/Indian config (격자·박스·weights)
├── run_all.py                                # 두 파이프라인 일괄 실행 / runner for both
├── requirements.txt
├── Design_SST.MD                             # SST pipeline 설계 문서
└── Design_Subsurface.MD                      # Subsurface pipeline 설계 문서
```

---

## 설치 순서 / Installation

### 1) 저장소 clone / Clone the repo

```powershell
git clone https://github.com/yowoo1201/Weather-Raw-Data-Process.git
cd Weather-Raw-Data-Process
```

### 2) Python 3.11+ 확인 / Confirm Python 3.11+

```powershell
python --version
```

3.11 미만이면 <https://www.python.org/downloads/>에서 최신 안정버전 설치.
If older than 3.11, install from python.org.

### 3) 가상환경(venv) 생성 / Create virtual environment

프로젝트 루트(`Code/` 또는 clone한 폴더)에서:
From the project root:

```powershell
python -m venv .venv
```

`.venv/` 폴더가 만들어집니다. **이 폴더는 `.gitignore`에 등록돼 있어 깃에 절대 커밋되지 않습니다** — 각 사용자의 로컬 머신마다 새로 만드는 것.

A `.venv/` directory is created. **It is in `.gitignore` and is never committed** — each user creates their own locally.

### 4) venv 활성화 / Activate the venv

**Windows PowerShell**:
```powershell
.\.venv\Scripts\Activate.ps1
```

처음 실행 시 실행 정책 오류가 나면 (only on first try, if blocked):
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**Windows Command Prompt (cmd)**:
```cmd
.venv\Scripts\activate.bat
```

**macOS / Linux**:
```bash
source .venv/bin/activate
```

활성화되면 프롬프트 앞에 `(.venv)`가 붙습니다. / Prompt shows `(.venv)` once active.

### 5) 의존성 설치 / Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

설치되는 패키지 / Installed packages:
- `numpy`, `pandas` — 데이터 처리 / data wrangling
- `matplotlib` — 시각화 / plotting
- `scipy` — 보간·회귀 / interpolation & regression
- `xarray`, `netCDF4` — OISST `.nc` 파일 읽기 / reading NetCDF

### 6) 입력 데이터 배치 / Place input data

#### Static 데이터 — 깃에 포함됨 (clone 시 자동) / Static — included in git

| 파일 / File | 비고 / Note |
|---|---|
| `Input/nino_clim_daily_1991-2020.csv` | SST: 4-box × 365일 climatology (1991–2020 기준) |
| `Input/data_p_clim_processed.tar.gz` | Pacific subsurface per-longitude DOY climatology |
| `Input/data_rama_clim_processed.tar.gz` (있을 때) | Indian subsurface per-longitude DOY climatology |

이들은 1991–2020 reference 기간에 의존하는 고정값이라 거의 변경 없음 — clone하면 그대로 사용 가능.
These are tied to the 1991–2020 reference period and rarely change — usable directly after clone.

#### Auto-fetch (네트워크 자동 다운로드 / fetched automatically)

`sst/run_pipeline.py` 실행 시 stage [0]에서 자동으로 받아 `Input/`에 덮어쓴다:
Auto-fetched at the start of `sst/run_pipeline.py`:

| 파일 / File | URL |
|---|---|
| `Input/wksst9120.for.txt` | <https://cpc.ncep.noaa.gov/data/indices/wksst9120.for> |
| `Input/rel_wksst9120.txt` | <https://cpc.ncep.noaa.gov/data/indices/rel_wksst9120.txt> |

네트워크 불가 + 기존 파일 존재 시 경고 후 기존 파일 사용. 둘 다 없으면 에러.
Network failure + existing file → warn and reuse; otherwise hard fail.

#### OISST 헬퍼 / OISST helper — `sst/fetch_oisst.py`

매일 OISST `.nc`를 받아 `Input/oisst_data/`를 최신 상태로 유지하는 보조 스크립트.
Companion script that pulls the latest OISST `.nc` files into `Input/oisst_data/`.

```powershell
python sst/fetch_oisst.py                          # 이번 달 + 지난 달 (default)
python sst/fetch_oisst.py --months 202604,202605   # 명시적으로
```

동작 / Behavior:
- 로컬에 없는 날짜: 다운로드 (final 우선, 없으면 preliminary)
- 로컬이 prelim뿐인데 remote에 final 있음: final 다운로드 + prelim 제거 (**overwrite preliminary by final**)
- 로컬에 final + prelim 둘 다 있음: prelim 정리 (final이 우선)
- 로컬이 이미 final: 스킵
- Local missing → download (prefer final, else prelim)
- Local prelim only, remote final available → fetch final and remove prelim
- Local has both final + prelim → drop the prelim
- Local already final → skip

날짜당 정확히 한 파일만 남도록 정리되어 SST 파이프라인이 중복 행을 만들지 않음.
Result: exactly one file per date, no double-counting in the SST pipeline.

#### Daily 데이터 — 모두 자동 fetch / Daily — all auto-fetched

| 파일 / File | 자동 단계 / Auto-fetched at | 출처 |
|---|---|---|
| `Input/oisst_data/oisst-avhrr-v02r01.YYYYMMDD.nc` | `sst/run_pipeline.py` stage [0b] | NOAA OISST v2.1 (<https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr/>) |
| `Input/data.tar` | `subsurface/run_subsurface.py` stage [0] (Pacific) | PMEL TAO/TRITON DDS form (<https://www.pmel.noaa.gov/tao/data_deliv/deliv-nojava-all.html>) |
| `Input/data_rama.tar` | `subsurface/run_subsurface.py` stage [0] (Indian) | PMEL RAMA DDS form (<https://www.pmel.noaa.gov/tao/data_deliv/deliv-nojava-rama.html>) |

각 fetch는 네트워크 실패 시 기존 로컬 파일이 있으면 그걸 사용 (warn).
Each fetch falls back to existing local file on network failure.

수동 호출도 가능 (date range / basin 선택):
Manual invocation:
```powershell
python sst/fetch_oisst.py --months 202604,202605
python subsurface/fetch_pmel.py --basin pacific --start 2026-04-01 --end 2026-12-31
python subsurface/fetch_pmel.py --basin indian
```

### 7) 실행 / Run

```powershell
# 전체 파이프라인
python run_all.py              # SST + Subsurface(Pacific+Indian) 모두 / all
python run_all.py sst          # SST만 / SST only
python run_all.py subsurface   # Subsurface만 (두 basin 모두) / Subsurface only

# Subsurface basin 선택
python subsurface/run_subsurface.py --basin pacific   # Pacific만
python subsurface/run_subsurface.py --basin indian    # Indian만
python subsurface/run_subsurface.py --basin both      # 둘 다 (default)
```

각 파이프라인은 시작 시 자기 출력 폴더를 비우고 새로 작성합니다 (overwrite 정책).
Subsurface는 basin별로 `Output/Subsurface/Pacific/` 와 `Output/Subsurface/Indian/`로 분리.
Each pipeline wipes its own output directory at start. Subsurface splits by basin.

해당 basin의 raw tarball이 `Input/`에 없으면 그 basin은 자동 skip하고 다른 basin은 계속 진행 (예: `data_rama.tar` 없으면 Pacific만 처리).
Missing a basin's raw tarball auto-skips that basin while the other still runs.

**실패 시 팝업** — 파이프라인이 비정상 종료(exit code != 0)하면 `run_all.py`가 tkinter 모달을
띄움. 터미널을 안 보고 있어도 즉시 알 수 있도록.
**Failure popup** — `run_all.py` shows a topmost tkinter messagebox if any
pipeline exits non-zero, so failures are visible without watching the terminal.

---

## 산출물 / Outputs

### SST (`Output/SST/`)
- `nino_rssta_daily.csv` — 일별 4-box rSSTA + 동서경사
- `nino_rssta_weekly.csv` — 주별 평균 + CPC 검증
- `nino_rssta_plot.png` — 4-panel 시계열
- `nino_gradients_plot.png` — 동서 경사 + climatology 비교
- `nino_report.md` — 자동 생성 markdown 보고서
- `nino_analysis.zip` — 위 5개 묶음

### Subsurface — Pacific (`Output/Subsurface/Pacific/`)
- `pac_5_anomaly_YYYYMMDD.png` × 15 — 일별 anomaly 단면도 (130°E–90°W, 0–350 m, ±5°)
- `pac_5_absolute_YYYYMMDD.png` × 15 — 일별 absolute T 단면도
- `pac_5_data.csv` — 일별 부이 T + climatology + anomaly
- `pac_5_plots.zip` — 모든 PNG + CSV
- 진단: 50–200 m anomaly 최댓값(Kelvin wave 동진), 28°C 동서폭(warm pool 경계)

### Subsurface — Indian (`Output/Subsurface/Indian/`)
- `ind_5_anomaly_YYYYMMDD.png` × 15 — 일별 anomaly 단면도 (40°E–100°E, 0–350 m, ±5°)
- `ind_5_absolute_YYYYMMDD.png` × 15 — 일별 absolute T 단면도
- `ind_5_data.csv` — 일별 부이 T + climatology + anomaly
- `ind_iod_boxes.csv` — WTIO/SETIO surface SSTA box-mean (일별)
- `ind_iod_timeseries.png` — DMI proxy(WTIO−SETIO) 15일 추이 + ±0.4/±1.0 °C 임계선
- `ind_5_plots.zip` — 위 PNG + CSV 모두
- 진단: WTIO/SETIO/DMI proxy, 적도 thermocline tilt(20°C isotherm at 60°E vs 90°E)

자세한 설계는 [Design_SST.MD](Design_SST.MD), [Design_Subsurface.MD](Design_Subsurface.MD) 참조.
See design docs for details.

---

## venv 관련 메모 / Notes on venv

- `.venv/`는 **절대 커밋 금지** — `.gitignore`에 등록됨. 머신·OS·Python 빌드에 종속적이라 다른 PC에서 작동 안 함.
- `.venv/` is **never committed** — it's machine/OS/Python-build specific.
- 패키지 추가/변경 시: `pip install <pkg>` → `pip freeze > requirements.txt` → 변경된 `requirements.txt`만 커밋.
- After adding packages: `pip install <pkg>` → `pip freeze > requirements.txt` → commit the updated `requirements.txt` only.
- venv 비활성화: `deactivate`
- Deactivate the venv: `deactivate`

---

## 트러블슈팅 / Troubleshooting

**`UnicodeEncodeError` (Windows 콘솔에서 한글/° 출력 깨짐)**
```powershell
$env:PYTHONUTF8 = "1"
python run_all.py
```
`run_all.py`는 자식 프로세스에 자동으로 `PYTHONUTF8=1`을 넘겨주지만, venv 활성화 PowerShell에서 직접 스크립트를 돌릴 때는 위 환경변수를 설정.

**`[skip] data.tar (or data_rama.tar) has no subsurface T-profile files`**
PMEL에서 받은 tarball이 표면 SST(`sst*_dy.ascii`)만 들어 있음. **subsurface 프로파일** (`t*_dy.ascii`)이 들어 있는 tarball로 교체. 한 basin이 skip돼도 다른 basin은 계속 진행됨.

**`[skip] data_rama.tar not found in Input/`**
Indian basin 입력이 없는 경우. Pacific만 돌리거나, RAMA tarball을 받아 `Input/data_rama.tar`로 배치.

**`No such file or directory: '.../oisst-avhrr-v02r01.*.nc'`**
`Input/oisst_data/`가 비었거나 파일명 패턴이 어긋남. NOAA OISST에서 일별 `.nc`를 받아 채워 넣을 것.
