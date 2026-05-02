# Weather-Raw-Data-Process

ENSO 일별 모니터링 파이프라인 — OISST 표면 SST와 TAO/TRITON 부이 subsurface 단면도를 함께 처리.

ENSO daily monitoring pipelines — OISST surface SST + TAO/TRITON buoy subsurface cross-sections.

---

## 구성 / Structure

```
Code/
├── Input/                    # 원천 데이터 (gitignored / not committed)
│   ├── oisst_data/*.nc       # NOAA OISST v2.1 AVHRR 일별 .nc
│   ├── nino_clim_daily_1991-2020.csv
│   ├── wksst9120.for.txt     # CPC 주간 절대 SSTA
│   ├── rel_wksst9120.txt     # CPC 주간 relative SSTA
│   ├── data.tar              # PMEL TAO/TRITON 부이 subsurface T-profile
│   └── data_p_clim_processed.tar.gz   # per-longitude DOY climatology
├── Output/                   # 자동 생성 (gitignored / regenerated each run)
│   ├── SST/                  # nino_*.csv, *.png, nino_report.md, .zip
│   └── Subsurface/           # pac_5_*.png, pac_5_data.csv, pac_5_plots.zip
├── sst/run_pipeline.py       # SST/rSSTA pipeline
├── subsurface/run_subsurface.py  # subsurface section pipeline
├── run_all.py                # 두 파이프라인 일괄 실행 / runner for both
├── requirements.txt
├── Design_SST.MD             # SST pipeline 설계 문서 / design doc
└── Design_Subsurface.MD      # Subsurface pipeline 설계 문서
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
| `Input/nino_clim_daily_1991-2020.csv` | 4-box × 365일 climatology (1991–2020 기준) |
| `Input/data_p_clim_processed.tar.gz` | per-longitude DOY climatology tarball |

이 둘은 1991–2020 reference 기간에 의존하는 고정값이라 거의 변경 없음 — clone하면 그대로 사용 가능.
These are tied to the 1991–2020 reference period and rarely change — usable directly after clone.

#### Daily/Weekly 데이터 — 직접 받아 넣을 것 / Daily/Weekly — fetch manually

매 실행 전 최신 본을 다음 위치에 배치:
Place fresh copies before each run:

| 파일 / File | 출처 / Source | 갱신 주기 |
|---|---|---|
| `Input/oisst_data/oisst-avhrr-v02r01.YYYYMMDD.nc` | NOAA OISST v2.1 (<https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr/>) | 매일 |
| `Input/wksst9120.for.txt` | CPC `wksst9120.for` (<https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for>) | 주간 |
| `Input/rel_wksst9120.txt` | CPC relative SSTA | 주간 |
| `Input/data.tar` | PMEL TAO/TRITON `t<lat><n\|s><lon><e\|w>_dy.ascii.gz` 묶음 (<https://www.pmel.noaa.gov/tao/data_deliv/deliv-nojava-all.html>) | 매일 |

### 7) 실행 / Run

```powershell
python run_all.py              # SST + Subsurface 모두 / both
python run_all.py sst          # SST만 / SST only
python run_all.py subsurface   # Subsurface만 / Subsurface only
```

각 파이프라인은 시작 시 자기 출력 폴더(`Output/SST/` 또는 `Output/Subsurface/`)를 비우고 새로 작성합니다 (overwrite 정책).
Each pipeline wipes its own output directory at start (overwrite policy).

---

## 산출물 / Outputs

### SST (`Output/SST/`)
- `nino_rssta_daily.csv` — 일별 4-box rSSTA + 동서경사
- `nino_rssta_weekly.csv` — 주별 평균 + CPC 검증
- `nino_rssta_plot.png` — 4-panel 시계열
- `nino_gradients_plot.png` — 동서 경사 + climatology 비교
- `nino_report.md` — 자동 생성 markdown 보고서
- `nino_analysis.zip` — 위 5개 묶음

### Subsurface (`Output/Subsurface/`)
- `pac_5_anomaly_YYYYMMDD.png` × 15 — 일별 anomaly 단면도
- `pac_5_absolute_YYYYMMDD.png` × 15 — 일별 absolute T 단면도
- `pac_5_data.csv` — 일별 부이 T + climatology + anomaly
- `pac_5_plots.zip` — 모든 PNG + CSV

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

**`ERROR: Input/data.tar contains no subsurface temperature profile files`**
PMEL에서 받은 tarball이 표면 SST(`sst*_dy.ascii`)만 들어 있음. **subsurface 프로파일** (`t*_dy.ascii`)이 들어 있는 tarball로 교체.

**`No such file or directory: '.../oisst-avhrr-v02r01.*.nc'`**
`Input/oisst_data/`가 비었거나 파일명 패턴이 어긋남. NOAA OISST에서 일별 `.nc`를 받아 채워 넣을 것.
