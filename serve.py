#!/usr/bin/env python3
"""
TKDFightStamp 本機服務(取代 python3 -m http.server)。

同時做兩件事:
  1. 服務靜態網頁(index.html / app.js / style.css / candidates.json)
  2. /api/merge 端點:收「時間戳+場次清單 + event_id」→ 本機爬對戰表 API
     (server 端沒有 CORS 問題)→ 合併補齊 → 回完整 YouTube 章節清單。

啟動:python3 serve.py   → http://localhost:8000
(需要能連 wego-tkd.zeabur.app;純本機,不對外。)
"""
import json, re, os, urllib.request, subprocess, threading, sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DET = os.path.join(HERE, "detector")
PY = os.path.join(DET, ".venv", "bin", "python")
WEGO = "https://wego-tkd.zeabur.app"
TONDAR = "https://www.tondar-cn.com/Competition"

# ---- 偵測 pipeline 進度狀態(給 /api/progress 輪詢)----
PROGRESS = {
    "running": False, "stage": "", "percent": 0, "msg": "",
    "done": False, "error": "", "result": "",  # result = 時間戳+場次清單
    "last_video": "", "last_round": 120, "last_model": "",  # 上次掃描,供重新過濾
}
PLOCK = threading.Lock()

# ---- 下載進度狀態(給 /api/dl_progress 輪詢,與偵測分開)----
DL = {"running": False, "percent": 0, "msg": "", "done": False, "error": "", "path": ""}
DLOCK = threading.Lock()
VIDEO_DIR = os.path.expanduser("~/Downloads/tkd_video")  # 影片下載目的地


def set_progress(**kw):
    with PLOCK:
        PROGRESS.update(kw)


def set_dl(**kw):
    with DLOCK:
        DL.update(kw)


def download_video(url, name):
    """背景執行緒:用 yt-dlp 下 1080p 到 VIDEO_DIR/name.mp4,解析進度。"""
    try:
        set_dl(running=True, done=False, error="", path="", percent=0, msg="啟動 yt-dlp...")
        os.makedirs(VIDEO_DIR, exist_ok=True)
        out_tmpl = os.path.join(VIDEO_DIR, f"{name}.%(ext)s")
        final = os.path.join(VIDEO_DIR, f"{name}.mp4")
        # --cookies-from-browser chrome:借用瀏覽器登入 cookie,通過 YouTube 的
        #   「Sign in to confirm you're not a bot」檢查(純本機 IP 多次請求會被擋)。
        cmd = ["yt-dlp", "-4", "--no-playlist", "--newline",
               "--cookies-from-browser", "chrome",
               "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                     "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
               "--merge-output-format", "mp4", "-o", out_tmpl, url]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            m = re.search(r"\[download\]\s+([\d.]+)%", line)
            if m:
                set_dl(percent=float(m.group(1)), msg=line.strip()[:120])
            elif "Merging" in line:
                set_dl(msg="合併影音中...")
        proc.wait()
        if proc.returncode == 0 and os.path.exists(final):
            set_dl(running=False, done=True, percent=100, msg="下載完成", path=final)
        else:
            set_dl(running=False, done=True, error=f"yt-dlp 退出碼 {proc.returncode}(檔案未產生)")
    except Exception as e:
        set_dl(running=False, done=True, error=str(e))


def run_stage(cmd, stage_label, env):
    """跑一個 pipeline 階段,解析其 PROGRESS 行更新全域進度。回傳 (ok, last_lines)。"""
    set_progress(stage=stage_label, percent=0, msg="啟動...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, env=env)
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        if len(tail) > 40:
            tail.pop(0)
        m = re.search(r"PROGRESS\s+([\d.]+)%", line)
        if m:
            set_progress(percent=float(m.group(1)), msg=line.split("PROGRESS", 1)[-1].strip())
    proc.wait()
    return proc.returncode == 0, "\n".join(tail)


# 賽事模型組:每個賽事的計分板樣式/機位不同,需各自的模型(見 MODEL_SETS)。
# 換新賽事時在此登記一組即可。
# 賽事模型組。hold=True → 掃描用「穩定待命→離開」判據(tondar 那台計分板開賽後
# 計時器 OCR 不穩,遞減鏈失效);hold=False → 原始遞減鏈判據(中正盃/國選舊機驗證基準)。
# skip_cls=True → 跳過第一回合分類器,掃描交界全給複審(計分板太小、OCR 太不穩,
#   分類器對該場地不可靠時用;你本來就人工複審,寧可多給不漏抓)。
MODEL_SETS = {
    "combined": {"sb": "scoreboard_combined", "cls": "round1_cls_combined", "label": "通用", "hold": True},
    "national": {"sb": "scoreboard_v1", "cls": "round1_cls_combined", "label": "國選(1號場地)", "hold": True},
    "zhongzheng": {"sb": "scoreboard_sb2", "cls": "round1_cls_sb2", "label": "中正盃", "hold": False},
    "venue2_nocls": {"sb": "scoreboard_combined", "cls": "round1_cls_combined", "label": "國選2號場地(小板,不分類)", "hold": True, "skip_cls": True},
}


def _pipeline_env(round_full, model_set, apply_filter):
    """組偵測子程序的環境變數(模型、賽制、HOLD、是否跳過分類)。回傳 (env, ms)。"""
    ms = MODEL_SETS.get(model_set, MODEL_SETS["national"])
    env = dict(os.environ, SB_MODEL=ms["sb"], CLS_MODEL=ms["cls"],
               SCALE_1080="1", ROUND_FULL=str(round_full))
    if ms.get("hold"):
        env["HOLD_MODE"] = "1"
    # 跳過分類:模型組預設 skip_cls,或使用者在 UI 關掉「套用第一回合過濾」。
    if ms.get("skip_cls") or not apply_filter:
        env["SKIP_CLS"] = "1"
    return env, ms


def _finalize_and_ocr(env, ms, video):
    """finalize(第一回合過濾/跳過)+ 場次 OCR。共用於首次偵測與重新過濾。"""
    out = os.path.join(DET, "out")
    crossings = os.path.join(out, "crossings_web.json")
    cands = os.path.join(out, "candidates_web.json")
    listtxt = os.path.join(out, "list_web.txt")
    dedup = "20" if ms.get("hold") else "100"
    ok, tail = run_stage([PY, os.path.join(DET, "finalize.py"), crossings, video, cands, dedup],
                         "第一回合過濾", env)
    if not ok:
        set_progress(running=False, done=True, error="finalize 階段失敗\n" + tail); return None
    ok, tail = run_stage([PY, os.path.join(DET, "list_matchnums.py"), cands, video, listtxt],
                         "場次 OCR", env)
    if not ok:
        set_progress(running=False, done=True, error="OCR 階段失敗\n" + tail); return None
    result = open(listtxt, encoding="utf-8").read()
    write_candidates_json(result)
    return result


def detect_pipeline(video, round_full, model_set="national", apply_filter=True):
    """背景執行緒:scan → finalize → OCR。影片路徑、賽制秒數、模型組由前端給。"""
    try:
        env, ms = _pipeline_env(round_full, model_set, apply_filter)
        crossings = os.path.join(DET, "out", "crossings_web.json")
        set_progress(running=True, done=False, error="", result="")
        # 1. 掃描交界(end=0 代表全片,scan_starts 會 ffprobe 取片長)
        ok, tail = run_stage([PY, os.path.join(DET, "scan_starts.py"), video, crossings,
                              str(round_full), "0", "0", "4"], "掃描開賽交界", env)
        if not ok:
            set_progress(running=False, done=True, error="掃描階段失敗\n" + tail); return
        # round_full=0(自動偵測)時,scan 已把實際判定值寫進 crossings_web.json。
        # 讀回來給 finalize 的 find_start_moment 用(否則 ROUND_FULL=0 會裁錯)。
        real_round = round_full
        try:
            real_round = int(json.load(open(crossings)).get("round_full_sec", round_full)) or round_full
        except Exception:
            pass
        env["ROUND_FULL"] = str(real_round)
        # 記住這次掃描用的影片/賽制/模型,供之後「重新過濾」不必重掃
        set_progress(last_video=video, last_round=real_round, last_model=model_set)
        # 2+3. finalize + OCR
        result = _finalize_and_ocr(env, ms, video)
        if result is None:
            return
        set_progress(running=False, done=True, percent=100, stage="完成",
                     msg="偵測完成", result=result)
    except Exception as e:
        set_progress(running=False, done=True, error=str(e))


def refilter_pipeline(model_set, apply_filter, video=""):
    """只重跑 finalize + OCR(用上次掃描的交界),供 UI 切換分類過濾而不重掃全片。
    video 前端帶來(比記憶體 last_video 可靠,server 重啟也能用);沒帶才 fallback。"""
    try:
        video = os.path.expanduser(video) or PROGRESS.get("last_video", "")
        round_full = PROGRESS.get("last_round", 120)
        crossings = os.path.join(DET, "out", "crossings_web.json")
        if not os.path.exists(crossings):
            set_progress(running=False, done=True, error="沒有上次的掃描結果可重新過濾,請先偵測一次")
            return
        if not video or not os.path.exists(video):
            set_progress(running=False, done=True, error=f"找不到影片檔(重新過濾需要):{video}")
            return
        env, ms = _pipeline_env(round_full, model_set, apply_filter)
        set_progress(running=True, done=False, error="", result="")
        result = _finalize_and_ocr(env, ms, video)
        if result is None:
            return
        set_progress(running=False, done=True, percent=100, stage="完成",
                     msg="重新過濾完成", result=result)
    except Exception as e:
        set_progress(running=False, done=True, error=str(e))


def _ts_to_sec(ts):
    p = [int(x) for x in ts.split(":")]
    if len(p) == 3:
        return p[0] * 3600 + p[1] * 60 + p[2]
    if len(p) == 2:
        return p[0] * 60 + p[1]
    return p[0]


def write_candidates_json(list_text):
    """把「時間戳\\t場次」清單轉成 candidates.json(前端「載入 AI 候選」讀的格式)。"""
    cands = []
    for raw in list_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("時間戳"):
            continue
        parts = re.split(r"[\s\t]+", line, maxsplit=1)
        try:
            t_sec = _ts_to_sec(parts[0])
        except Exception:
            continue
        cands.append({"t_sec": t_sec, "matchnum": parts[1].strip() if len(parts) > 1 else ""})
    with open(os.path.join(HERE, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump({"candidates": cands}, f, ensure_ascii=False, indent=2)

# ---- 對戰表合併邏輯(從 detector/merge.py 移植)----
TEAM_PATTERNS = [
    r"^(?:臺|台)北市立", r"^(?:新北|臺北|台北|新竹|基隆|台中|臺中|桃園|臺南|台南|高雄|嘉義)市立",
    r"^(?:臺|台)北縣立", r"^新竹縣立", r"^屏東縣立", r"^宜蘭縣立", r"^花蓮縣立",
    r"^(?:台|臺)東縣立", r"^苗栗縣立", r"^彰化縣立", r"^南投縣立", r"^雲林縣立", r"^嘉義縣立",
    r"^(?:新北|臺北|台北|新竹|基隆|台中|臺中|桃園|臺南|台南|高雄)市", r"^(?:臺|台)北縣",
    r"^基隆市", r"^新竹縣", r"^屏東縣", r"^宜蘭縣", r"^花蓮縣", r"^台東縣", r"^臺東縣",
    r"^苗栗縣", r"^彰化縣", r"^南投縣", r"^雲林縣", r"^嘉義縣", r"^嘉義市",
]


def wego_api(path):
    req = urllib.request.Request(WEGO + path, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=25))


def simplify_team(team):
    t = (team or "").strip()
    for p in TEAM_PATTERNS:
        t = re.sub(p, "", t)
    return t.strip() or (team or "").strip()


def simplify_category(name):
    t = (name or "").strip()
    level = ""
    if "青少年" in t or "國中" in t:
        level = "(國)"
    elif "青年" in t or "高中" in t or "社會" in t:
        level = "(社高)"
    gender = "女子" if "女" in t else ("男子" if "男" in t else "")
    m = re.search(r"(\d+)KG(以上)?", t)
    weight = (m.group(1) + "KG" + ("+" if m.group(2) else "")) if m else ""
    return f"{level}{gender}{weight}" if (gender and weight) else t


def build_index(event_id):
    idx = {}
    for cat in wego_api(f"/api/public/events/{event_id}/categories"):
        try:
            sch = wego_api(f"/api/public/events/{event_id}/categories/{cat['id']}/schedule")
        except Exception:
            continue
        for m in sch.get("data", []):
            num = str(m.get("matchnumber", "")).strip()
            if num:
                m["_category"] = cat["name"]
                idx[num] = m
    return idx


def format_line(ts, num, m):
    if m is None:
        return f"{ts} {num} (對戰表查無此場次)"
    p1 = f"{simplify_team(m.get('p1_team',''))} {m.get('p1_display','')}".strip()
    p2 = f"{simplify_team(m.get('p2_team',''))} {m.get('p2_display','')}".strip()
    w = m.get("winner_id")
    if w and w == m.get("player1_id"):
        p1 += " (勝)"
    elif w and w == m.get("player2_id"):
        p2 += " (勝)"
    return f"{ts} {num} {p1} vs {p2} | {simplify_category(m.get('_category',''))} | {m.get('round','')}"


def merge_list(event_id, text):
    idx = build_index(event_id)
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("時間戳"):
            continue
        parts = re.split(r"[\s\t]+", line, maxsplit=1)
        ts = parts[0]
        num = parts[1].strip() if len(parts) > 1 else ""
        out.append(format_line(ts, num, idx.get(num) if num else None))
    return "\n".join(out)


# ---- tondar-cn 對戰表(www.tondar-cn.com,三段式 AJAX PHP)----
# 場次號 4 碼 [場地][天][兩碼流水],例 1301 = 第1場地 第3天 第01場。
# 日期 EDte 為民國格式(1150512)。合併時鎖定單日、掃全部場地建索引。

def tondar_api(endpoint, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"{TONDAR}/{endpoint}", data=data,
        headers={"User-Agent": "Mozilla/5.0",
                 "Referer": f"{TONDAR}/ScheduleC.php?EventNo={params.get('EventNo','')}"})
    return json.load(urllib.request.urlopen(req, timeout=25))


def tondar_dates(event_no):
    """回傳該賽事的比賽日期清單(民國格式字串)。"""
    return [d["Value"] for d in tondar_api("Return_KyoDte.php", {"EventNo": event_no})]


def tondar_category(grade, weight):
    """青少年→(國)、青年→(社高);-44公斤級→44KG、+59公斤級→59KG+。"""
    level = "(國)" if "青少年" in grade else ("(社高)" if "青年" in grade else "")
    gender = "女子" if "女" in grade else ("男子" if "男" in grade else "")
    m = re.search(r"([+\-]?)(\d+)公斤級", weight or "")
    wt = (m.group(2) + "KG" + ("+" if m.group(1) == "+" else "")) if m else (weight or "")
    return f"{level}{gender}{wt}".strip()


TONDAR_SYSTEM = {"R64": "64強", "R32": "32強", "R16": "16強", "R8": "8強",
                 "四強賽": "4強", "冠亞軍": "冠亞軍", "敗部": "敗部復活"}


def tondar_build_index(event_no, edte):
    """鎖定單日,掃 1~5 號場地,以 Match 場次號建索引。"""
    idx = {}
    for court in range(1, 6):
        try:
            rows = tondar_api("Return_ScheduleC.php",
                              {"EventNo": event_no, "EDte": edte, "ECourt": court})
        except Exception:
            continue
        for m in rows:
            num = str(m.get("Match", "")).strip()
            if num:
                idx[num] = m
    return idx


def tondar_format_line(ts, num, m):
    if m is None:
        return f"{ts} {num} (對戰表查無此場次)"
    blue = f"{simplify_team(m.get('Blue_Dptname',''))} {m.get('Blue','')}".strip()
    red = f"{simplify_team(m.get('Red_Dptname',''))} {m.get('Red','')}".strip()
    win = m.get("Win", "")
    if win == "B":
        blue += " (勝)"
    elif win == "R":
        red += " (勝)"
    cat = tondar_category(m.get("EGrade", ""), m.get("EWeight", ""))
    rnd = TONDAR_SYSTEM.get(m.get("ESystem", ""), m.get("ESystem", ""))
    return f"{ts} {num} {blue} vs {red} | {cat} | {rnd}"


def tondar_merge_list(event_no, edte, text):
    idx = tondar_build_index(event_no, edte)
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("時間戳"):
            continue
        parts = re.split(r"[\s\t]+", line, maxsplit=1)
        ts = parts[0]
        num = parts[1].strip() if len(parts) > 1 else ""
        out.append(tondar_format_line(ts, num, idx.get(num) if num else None))
    return "\n".join(out)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        # 強制不快取,避免瀏覽器用到舊的 app.js/index.html(反覆中快取的根治)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _send_json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/progress":
            with PLOCK:
                self._send_json(dict(PROGRESS))
            return
        if p == "/api/dl_progress":
            with DLOCK:
                self._send_json(dict(DL))
            return
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n) or b"{}") if n else {}

        if path == "/api/merge":
            source = str(payload.get("source", "wego")).strip()
            text = payload.get("text", "")
            print(f"[/api/merge] source={source} 行數={len(text.splitlines())}", flush=True)
            try:
                if source == "tondar":
                    event_no = str(payload.get("event_no", "")).strip()
                    edte = str(payload.get("edte", "")).strip()
                    self._send_json({"ok": True, "result": tondar_merge_list(event_no, edte, text)})
                else:
                    event_id = str(payload.get("event_id", "")).strip()
                    self._send_json({"ok": True, "result": merge_list(event_id, text)})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
            return

        if path == "/api/tondar_dates":
            event_no = str(payload.get("event_no", "")).strip()
            try:
                self._send_json({"ok": True, "dates": tondar_dates(event_no)})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
            return

        if path == "/api/detect":
            video = os.path.expanduser(str(payload.get("video", "")).strip())
            round_full = int(payload.get("round_full", 90))
            model_set = str(payload.get("model_set", "national")).strip()
            apply_filter = bool(payload.get("apply_filter", True))
            if PROGRESS["running"]:
                self._send_json({"ok": False, "error": "偵測進行中,請等目前這支跑完"}); return
            if not video or not os.path.exists(video):
                self._send_json({"ok": False, "error": f"找不到影片檔:{video}"}); return
            print(f"[/api/detect] video={video} round_full={round_full} model_set={model_set} filter={apply_filter}", flush=True)
            threading.Thread(target=detect_pipeline, args=(video, round_full, model_set, apply_filter), daemon=True).start()
            self._send_json({"ok": True, "msg": "偵測已開始"})
            return

        if path == "/api/refilter":
            # 只重跑 finalize+OCR(用上次掃描交界),供切換「套用第一回合過濾」而不重掃全片。
            model_set = str(payload.get("model_set", PROGRESS.get("last_model") or "national")).strip()
            apply_filter = bool(payload.get("apply_filter", True))
            video = str(payload.get("video", "")).strip()
            if PROGRESS["running"]:
                self._send_json({"ok": False, "error": "處理進行中,請稍候"}); return
            print(f"[/api/refilter] model_set={model_set} filter={apply_filter} video={video}", flush=True)
            threading.Thread(target=refilter_pipeline, args=(model_set, apply_filter, video), daemon=True).start()
            self._send_json({"ok": True, "msg": "重新過濾已開始"})
            return

        if path == "/api/download":
            url = str(payload.get("url", "")).strip()
            # 檔名:優先用前端給的,否則從網址取 video id
            name = str(payload.get("name", "")).strip()
            if not name:
                m = re.search(r"(?:v=|youtu\.be/|/live/|/shorts/)([A-Za-z0-9_-]{11})", url)
                name = m.group(1) if m else "video"
            name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)  # 清成安全檔名
            if DL["running"]:
                self._send_json({"ok": False, "error": "下載進行中,請等目前這支完成"}); return
            if not url:
                self._send_json({"ok": False, "error": "請填 YouTube 網址"}); return
            print(f"[/api/download] url={url} name={name}", flush=True)
            threading.Thread(target=download_video, args=(url, name), daemon=True).start()
            self._send_json({"ok": True, "msg": "下載已開始"})
            return

        self.send_response(404); self.end_headers()


if __name__ == "__main__":
    print("TKDFightStamp 服務啟動:http://localhost:8000")
    print("(含對戰表合併 proxy;純本機)")
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
