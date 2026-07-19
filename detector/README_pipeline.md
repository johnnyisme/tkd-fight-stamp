# TKD 開賽時間戳偵測 Pipeline

自動從跆拳道比賽影片抓出「每場開賽時間點」,產出 candidates.json 供 TKDFightStamp 網站複核。

## 原理(一句話)

開賽 = 記分板計時器從滿值(2:00 或 1:30)開始倒數那一刻(2:00→1:59 交界)。
用 YOLO 偵測記分板 → OCR 讀計時器 → 狀態追蹤找「待命段結束」= 所有回合起跑 →
第一回合分類器濾掉第 2/3 回合 → 每場一個開賽點。

（手勢/裁判偵測那條路試過但放棄，計時器交界法更可靠。referee_v1 模型留著但未用。）

## 兩種使用方式

1. **網站一站式(推薦,不碰終端機)** — 見下方「網站一站式流程」。下載→偵測→複審→匯出全在 http://localhost:8000 內完成。
2. **命令列 per-video** — 訓練新賽事模型、或想逐階段掌控時用,見下方「命令列:訓練新賽事模型」。

## 環境

- venv: `detector/.venv`(torch 2.13 + ultralytics + easyocr + opencv,MPS 加速)
- 影片放本機。YouTube 下載用 `yt-dlp -4 --cookies-from-browser chrome`:
  - `-4` 強制 IPv4,繞過本機 IPv6 卡在 "Downloading webpage"
  - `--cookies-from-browser chrome` 借瀏覽器登入 cookie,通過 YouTube「Sign in to confirm you're not a bot」檢查(純本機 IP 多次請求會被擋)。需 Chrome 已登入 YouTube。

## 網站一站式流程

**啟動(務必用 venv 的 python,不是 `python3 -m http.server`)**:
```
cd <專案根> && detector/.venv/bin/python serve.py   # http://localhost:8000
```
serve.py 同時服務網頁 + 提供 API:`/api/download`(yt-dlp 下載)、`/api/detect`(偵測 pipeline)、
`/api/progress`+`/api/dl_progress`(進度輪詢)、`/api/merge`(對戰表合併)、`/api/tondar_dates`(tondar 日期)。

網站「① 偵測開賽時間」由上到下:
1. **下載影片**:貼 YouTube 網址 → 按「下載影片」→ 1080p 存到 `~/Downloads/tkd_video/`,
   進度條顯示 %/速度/ETA;完成後路徑自動填入下方「本機影片路徑」。
2. **偵測**:選賽事模型(通用/國選/中正盃)+ 回合秒數(2:00 或 1:30)→ 按「開始偵測」。
   三階段進度條(掃描交界→第一回合過濾→場次OCR),完成後時間戳自動填入「② 複審」。
3. **② 複審**:逐場跳轉對照影片,改時間/改場次/插入/刪除(OCR 常讀不到場次號,手 key)。
4. **③ 匯出**:對戰表來源選 wego-tkd 或 tondar-cn:
   - tondar-cn:填 EventNo(如 18)→「載入日期」→ 選比賽日 → 「補齊對戰資訊」
   - 產出完整 YouTube 章節清單 → 複製/下載。

## 命令列:訓練新賽事模型

（以下為訓練新賽事模型的流程。已知賽事直接用網站選對應模型即可,不必重跑。）

### 模型組(serve.py 的 MODEL_SETS,前端下拉對應)
| 選項 | scoreboard | round1_cls | hold | 說明 |
|---|---|---|---|---|
| 通用 | `scoreboard_combined` | `round1_cls` | ✓ | 國選+中正盃 116 張合訓,mAP50 0.995,對兩賽事都最穩,新賽事優先試這個 |
| 國選 | `scoreboard_v1` | `round1_cls` | ✓ | 國選專用(tondar 那台計分板) |
| 中正盃 | `scoreboard_sb2` | `round1_cls_sb2` | ✗ | 中正盃專用 |

**HOLD_MODE(掃描判據,由模型組的 `hold` 決定,serve.py 帶環境變數 `HOLD_MODE=1`)**:
- `hold=✗`(中正盃):原始「遞減鏈」判據 —— 開賽後計時器 OCR 讀得準,靠 2:00→1:59→1:58… 單調遞減鏈確認。
- `hold=✓`(國選/tondar):「穩定待命→離開」判據 —— 那台計分板開賽後夾 3-2-1 倒數、計時器十位常被漏讀,遞減鏈失效;改判「開賽前有夠長穩定滿值待命」。寬鬆(寧可多抓假點、不漏抓),殘留假點靠 finalize 小 dedup + 人工複審刪。
- 兩條判據在 scan_starts.py 內以 `HOLD_MODE` gate 隔離,互不影響。

### 模型命名慣例(訓練新賽事時)
- 每個賽事一套模型:`scoreboard_<賽事>`、`round1_cls_<賽事>`
- 累積多賽事後,把各賽事 dataset 合併(見 `dataset_combined/`)重訓一個通用模型,泛化力優於單賽事模型。

### Step 1 — 抽訓練幀 + 標記記分板
```
# 抽幀(4K 影片加 scale=1920:1080)
for t in $(seq 300 360 <片長>); do ffmpeg -ss $t -i <影片> -frames:v 1 -vf scale=1920:1080 -q:v 3 dataset_XX/images/f_t$t.jpg; done
# 起標記網頁(改 dataset 路徑 + port),你拉框框住記分板
.venv/bin/python label_server_XX.py   # http://localhost:80XX
```

### Step 2 — 訓練記分板偵測器
```
# 切 train/val(每5取1當val)+ 建 scoreboard.yaml,然後:
yolo detect train model=yolo11n.pt data=dataset_XX/scoreboard.yaml epochs=80 imgsz=960 device=mps name=scoreboard_XX
# 驗證 holdout 幾幀框得準(目標 mAP50 > 0.95)
```

### Step 3 — 全片交界掃描(自動偵測賽制)
先讀幾幀計時器看滿值是 120(2:00) 還是 90(1:30),定 round_full。
```
SB_MODEL=scoreboard_XX SCALE_1080=1 .venv/bin/python scan_starts.py <影片> out/crossings_XX.json <round_full> 0 <片長> 4
# 產出所有回合起跑交界(含第1/2/3回合)
```

### Step 4 — 抽交界記分板 + 標記第一回合
```
# 裁每個交界的記分板(crop_crossings.py 改模型/影片路徑)
.venv/bin/python crop_crossings.py out/crossings_XX.json dataset_round_XX/images
# 起點選標記網頁,你點「回合格全零 000:000」= 第一回合那些
.venv/bin/python label_round_XX.py   # http://localhost:80XX
```

### Step 5 — 訓練第一回合分類器
```
# 切 cls_data/{train,val}/{round1,other},然後:
yolo classify train model=yolo11n-cls.pt data=dataset_round_XX/cls_data epochs=60 imgsz=224 device=mps name=round1_cls_XX
```

### Step 6 — 整合產出候選
```
# finalize.py 改用 scoreboard_XX + round1_cls_XX 模型
# dedup:HOLD_MODE 賽事(開賽後 OCR 不穩)用小值 20,只併完全重複、不讓假點吃掉相鄰真開賽;
#        其他用 100(同場多回合合併)。serve.py 已依模型組自動帶對的 dedup。
[HOLD_MODE=1] SB_MODEL=scoreboard_XX CLS_MODEL=round1_cls_XX SCALE_1080=1 \
  .venv/bin/python finalize.py out/crossings_XX.json <影片> out/candidates_XX.json <dedup>
# 內含 -2 秒 offset(寧早勿晚)
```

### Step 7 — 登記到 serve.py 並在網站用
在 serve.py 的 `MODEL_SETS` 加一組 `{"sb":"scoreboard_XX","cls":"round1_cls_XX","hold":...}`,
前端「賽事模型」下拉即多一個選項。之後走上方「網站一站式流程」即可,不再需要命令列。

## 已知限制(誠實)

- **換賽事仍可能要重訓**:記分板樣式(配色/版面/角度)差異大時,單賽事模型不通用。已合併國選+中正盃訓出 `scoreboard_combined` 通用模型(mAP50 0.995),新賽事優先試它;泛化力隨累積賽事數提升,但**不保證免重訓**,樣式差太多仍需補標 10-20 張微調。
- **開賽後計時器 OCR 因機而異**:中正盃那台讀得準(用遞減鏈);國選/tondar 那台開賽夾 3-2-1 倒數 + 十位漏讀,遞減鏈失效 → 改用 HOLD_MODE「穩定待命→離開」判據。換新賽事若掃出來大量漏抓,先看是不是該開/關 HOLD_MODE。
- **HOLD_MODE 寧可多抓**:會殘留少量待命抖動假點(複審時跳過去看一眼即刪)。這是刻意取捨 —— 假點可刪,漏抓無從得知。
- **小數字 OCR 讀不動**:MATCH 編號/ROUND 數字太小,OCR 不可靠 → 用「第一回合影像分類器」繞過回合判斷;場次號則由人工複審手 key。
- **落點 ±2 秒**:交界 OCR 有 1-2 秒抖動,統一 -2 秒(寧早勿晚),精確到秒交給網站人工微調。
- **跳場/別場地**:動態賽程會跳號、分場地。本片沒打的場次不會有 2:00 起跑,自然不出現(不誤報)。
- **YouTube 下載需 Chrome cookie**:純本機 IP 多次請求會被 YouTube 擋(bot 檢查),靠 `--cookies-from-browser chrome` 通過,需 Chrome 已登入。連續大量下載仍可能觸發限流。
