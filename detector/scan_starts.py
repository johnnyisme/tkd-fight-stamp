#!/usr/bin/env python3
"""
全片找「每場真開賽」= 計時器 2:00→1:59 交界，經回合聚類後取每場最早者。

原理（經 user 確認）：
  - 開賽/每回合起跑，計時器都會 2:00→1:59。所以全片會有多個交界(每場3回合)。
  - 同一場三回合連續打完，中間只有短休息，不插別場。
  - 故：把所有交界按時間排序，相鄰 < GAP 秒者聚為同一場，取每群最早交界 = 該場開賽。
  - 別場地/跳場的比賽在本片沒有 2:00 起跑，自然不會出現 → 不會誤報。

兩段式：
  Pass1 粗掃(step=COARSE)讀計時器，標記「滿值(2:00)待命」與「已跑(<滿值)」。
  Pass2 對每個「待命→已跑」的邊界，1 秒細找精確交界。
  再聚類取每場最早。

用法：scan_starts.py <video> <out_json> <round_full> [start] [end] [coarse] [gap]
"""
import sys, os, json, subprocess, tempfile
import cv2
from ultralytics import YOLO
import easyocr

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = YOLO(os.path.join(HERE, "runs", os.environ.get("SB_MODEL", "scoreboard_v1"), "weights", "best.pt"))
# 4K 影片需縮到 1080p(模型在 1080p 幀上訓練)。設 SCALE_1080=1 開啟。
SCALE = os.environ.get("SCALE_1080") == "1"
_reader = None
_tmp = tempfile.mktemp(suffix=".jpg")


def reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en"], gpu=True)
    return _reader


def parse_timer(text, round_full=120):
    """強化版:重點是判斷「是否已開跑(<滿值)」,容忍開賽瞬間 OCR 少讀冒號/多讀雜字。"""
    t = text.strip().replace(" ", "").replace(":", "")
    if not t.isdigit():
        return None
    n = len(t)
    cands = []
    if n == 3:      # '159'->1:59, '200'->2:00
        cands.append(int(t[0]) * 60 + int(t[1:]))
    elif n == 4:    # '1655'(冒號被讀成數字)-> 首位:末兩位
        cands.append(int(t[0]) * 60 + int(t[2:]))
    elif n == 2:    # 秒數
        cands.append(int(t))
    elif n == 1:
        cands.append(int(t))
    valid = [c for c in cands if 0 <= c <= round_full + 2]
    return valid[0] if valid else None


def pick_board(boxes, W, H):
    """從偵測到的計分板挑「本場地主用台」。
    排除貼著畫面邊緣的 box —— 被切邊 = 資訊不完整 = 通常是別場地/轉播台,完全捨棄
    (user 確認:只有部份資訊的那台要丟)。剩下的取 conf 最高。全被排除才 fallback 用原始最高。"""
    margin = 8  # 距畫面邊 <margin px 視為切邊
    full = [b for b in boxes
            if b.xyxy[0][0] > margin and b.xyxy[0][1] > margin
            and b.xyxy[0][2] < W - margin and b.xyxy[0][3] < H - margin]
    pool = full if full else list(boxes)
    return max(pool, key=lambda b: float(b.conf))


def timer_at(video, t):
    cmd = ["ffmpeg", "-y", "-ss", str(t), "-i", video, "-frames:v", "1"]
    if SCALE:
        cmd += ["-vf", "scale=1920:1080"]
    cmd += ["-q:v", "2", _tmp]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frame = cv2.imread(_tmp)
    if frame is None:
        return None, 0.0
    r = BOARD.predict(frame, conf=0.25, device="mps", verbose=False)[0]
    if len(r.boxes) == 0:
        return None, 0.0
    H, W = frame.shape[:2]
    b = pick_board(r.boxes, W, H)
    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
    H, W = frame.shape[:2]; pad = 6
    bd = frame[max(0,y1-pad):min(H,y2+pad), max(0,x1-pad):min(W,x2+pad)]
    h, w = bd.shape[:2]
    sub = bd[int(0.52*h):int(0.74*h), int(0.36*w):int(0.64*w)]
    if sub.size == 0:
        return None, 0.0
    sub = cv2.resize(sub, (sub.shape[1]*4, sub.shape[0]*4), interpolation=cv2.INTER_CUBIC)
    res = reader().readtext(sub, allowlist="0123456789:", detail=1, paragraph=False)
    best, bc = None, 0.0
    for (_, txt, conf) in res:
        sec = parse_timer(txt)
        if sec is not None and conf > bc:
            best, bc = sec, conf
    return best, bc


def detect_round_full(video, end, samples=60):
    """自動判賽制:抽 samples 幀讀計時器,看讀到的最大值接近 90(1:30)還是 120(2:00)。
    避免使用者手選回合秒數選錯(選錯會整支掃不到,且不報錯)。回傳 90 或 120。"""
    step = max(1, end // samples)
    votes90 = votes120 = 0
    seen_max = 0
    for t in range(5, end, step):
        # timer_at 內部用 parse_timer 預設上限 120,涵蓋 90/120 兩種賽制
        sec, conf = timer_at(video, t)
        if sec is None or conf < 0.6:
            continue
        seen_max = max(seen_max, sec)
        if 88 <= sec <= 92:
            votes90 += 1
        elif 118 <= sec <= 122:
            votes120 += 1
    # 有明確 2:00 讀數 → 120;否則若看過接近 90 的滿值 → 90;都沒有預設 120
    guess = 120 if votes120 >= max(1, votes90) else 90
    print(f"AUTO round_full: 讀到最大值 {seen_max//60}:{seen_max%60:02d}, "
          f"votes(90={votes90},120={votes120}) → 判定 {guess}", flush=True)
    return guess


def main():
    video = sys.argv[1]; out_json = sys.argv[2]; round_full = int(sys.argv[3])
    start = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    end = int(sys.argv[5]) if len(sys.argv) > 5 else None
    coarse = int(sys.argv[6]) if len(sys.argv) > 6 else 5
    gap = int(sys.argv[7]) if len(sys.argv) > 7 else 240  # 同場聚類間隔(秒)
    if end is not None and end <= 0:
        end = None  # end<=0 視為「全片」(serve.py 傳 0 用此)
    if end is None:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", video],
                           stdout=subprocess.PIPE, text=True)
        end = int(float(r.stdout.strip()))
    reader()
    # round_full=0 → 自動偵測賽制(90 或 120),免得手選選錯整支掃不到
    if round_full == 0:
        print("PROGRESS 0.0%  自動偵測賽制中...", flush=True)
        round_full = detect_round_full(video, end)
    # HOLD_MODE=1 走 tondar 那台計分板的判據(開賽後 OCR 不穩,靠「穩定待命→離開」)。
    # 預設(不設)= 中正盃/國選舊機驗證過的原判據,完全不動,保住既有結果。
    hold_mode = os.environ.get("HOLD_MODE") == "1"
    if hold_mode:
        # tondar:ready 只認滿值(1:59~2:02),run = 低於滿值即算,消除死區。
        lo_ready, hi_ready = round_full - 1, round_full + 2
        running_hi = round_full - 1
    else:
        # 原版(中正盃/國選舊機驗證基準,勿動):
        lo_ready, hi_ready = round_full - 2, round_full + 2  # 滿值容許
        running_hi = round_full - 4  # 「已跑」判定

    # Pass1: 粗掃,記錄每個取樣點狀態
    total_samples = (end - start) // coarse + 1
    print(f"PASS1 coarse {start}..{end} step={coarse} (共 {total_samples} 取樣點)", flush=True)
    states = []  # (t, 'ready'|'run'|None)
    t = start
    done = 0
    import time as _time
    t0 = _time.time()
    while t <= end:
        sec, conf = timer_at(video, t)
        st = None
        if sec is not None and conf >= 0.5:
            if lo_ready <= sec <= hi_ready:
                st = "ready"
            elif sec < running_hi:
                st = "run"
        states.append((t, sec, conf, st))
        done += 1
        # 進度:每 25 點印一次(含 %、目前掃到的影片時間、速度、預估剩餘)
        if done % 25 == 0 or t + coarse > end:
            pct = 100 * done / total_samples
            elapsed = _time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total_samples - done) / rate if rate > 0 else 0
            ready_so_far = sum(1 for s in states if s[3] == "ready")
            print(f"PROGRESS {pct:5.1f}%  {done}/{total_samples}  "
                  f"影片位置 {t//3600:02d}:{t%3600//60:02d}:{t%60:02d}  "
                  f"速度 {rate:.1f}點/秒  剩約 {int(eta//60)}分{int(eta%60)}秒  "
                  f"ready點 {ready_so_far}", flush=True)
        t += coarse
    # 狀態追蹤法(依 user 邏輯):找「穩定 2:00 待命段」的結束 = 開賽。
    # 待命段可任意長(選手/設備等待很久是正常),中間 OCR None/抖動都忽略,仍算待命。
    # 只有「往後連續確認真的在跑(<滿值且遞減)」才判待命段結束 → 該結束點 = 開賽。
    # 這樣待命期多長都不怕,單幀抖動也不會誤判(要連續確認)。
    # PASS1 只負責「圈出待命段結束的候選邊界」(寬鬆,寧可多)。
    # 真正的遞減驗證搬到 PASS2 用 1 秒密集取樣做 —— 因為粗掃(4秒)在開賽後 OCR 稀疏,
    # 遞減鏈湊不滿會誤丟真開賽(306/311/322 漏抓的原因)。
    # 邊界定義:一段 ready(滿值待命) 之後,計時器「離開滿值」(出現 run,或連續讀不到滿值)。
    boundaries = []  # (最後ready時間, 邊界後時間)
    in_ready = False
    last_ready_t = None
    i = 0
    while i < len(states):
        t_i, sec_i, conf_i, st_i = states[i]
        if st_i == "ready":
            in_ready = True
            last_ready_t = t_i
            i += 1
            continue
        if in_ready and st_i == "run":
            # 待命段結束的候選邊界 → 丟給 PASS2 密集驗證
            boundaries.append((last_ready_t, t_i))
            in_ready = False
        i += 1
    print(f"PASS1: {len(boundaries)} candidate boundaries (待PASS2密集驗證)", flush=True)

    # Pass2: 對每個候選邊界,用 1 秒密集取樣判斷真開賽。
    # 兩種判據取其一成立即算真開賽(涵蓋不同計分板行為):
    #   (A) 遞減鏈:交界後單調遞減鏈 ≥4 點、跨 ≥10 秒。
    #       適用「開賽後計時器 OCR 讀得準」的計分板(國選舊機/中正盃)。
    #   (B) 穩定待命→離開:交界前有穩定高信心滿值待命(≥4 個 conf≥0.6 的滿值點),
    #       之後離開滿值且不再回到穩定待命。適用「開賽後計時器 OCR 不穩」的計分板
    #       (tondar 這台:開賽夾 3-2-1 倒數 + 十位數常被漏讀成 0:57,遞減鏈湊不成)。
    # 只要任一成立就收,兼顧 recall(避免漏抓)與擋假點(要有穩定待命,純雜訊不會過)。
    print(f"PASS2 密集驗證 {len(boundaries)} 個邊界", flush=True)
    crossings = []
    import time as _t2
    p2t0 = _t2.time()
    for bi, (ta, tb) in enumerate(boundaries, 1):
        # 密集讀窗:原版 ta-2(勿動,保中正盃基準);HOLD_MODE 往前多讀 8 秒判穩定待命。
        lo_t = max(0, ta - (8 if hold_mode else 2))
        hi_t = min(end, tb + 35)
        seq = {}
        for tt in range(lo_t, hi_t + 1):
            sec, conf = timer_at(video, tt)
            seq[tt] = (sec, conf)
        # 找最後一個滿值(待命)時刻
        last_ready = None
        for tt in range(lo_t, hi_t + 1):
            s, c = seq[tt]
            if s is not None and c >= 0.5 and lo_ready <= s <= hi_ready:
                last_ready = tt
        if last_ready is None:
            continue
        cross = last_ready + 1  # 交界 = 最後滿值的下一秒

        # 判據 A:遞減鏈(中正盃/國選舊機 —— 開賽後計時器 OCR 讀得準)
        pts = []
        for tt in range(cross, hi_t + 1):
            s, c = seq[tt]
            if s is not None and c >= 0.5 and 0 < s <= round_full + 2:
                pts.append((tt, s))
        best_len, best_span = 0, 0
        for a in range(len(pts)):
            chain = [pts[a]]
            for b in range(a + 1, len(pts)):
                if pts[b][1] <= chain[-1][1] - 2:
                    chain.append(pts[b])
            if len(chain) > best_len:
                best_len = len(chain); best_span = chain[0][1] - chain[-1][1]
        chain_ok = best_len >= 4 and best_span >= 10

        # 判據 B(HOLD_MODE,tondar 那台開賽後 OCR 不穩,遞減鏈失效):
        # 依 user 需求「寧可多抓假點、不要漏抓」—— 只要求開賽前有夠長穩定滿值待命。
        # 殘留的待命抖動假點(如 19:52)由後面 MERGE_GAP 併掉同場重複 + 複審時人工刪。
        # (曾試「離開不回頭」嚴格濾假點,但會漏掉真開賽 20:42 + cross 飄移,違反不漏抓原則。)
        hold = 0
        for tt in range(last_ready, lo_t - 1, -1):
            s, c = seq[tt]
            if s is not None and c >= 0.6 and lo_ready <= s <= hi_ready:
                hold += 1
            elif s is not None and c >= 0.6:
                break  # 高信心讀到非滿值 → 待命段中斷
        hold_ok = hold_mode and hold >= 4

        # 非 HOLD_MODE(中正盃/國選舊機):只認遞減鏈,與原版完全一致。
        if chain_ok or hold_ok:
            crossings.append(cross)
        if bi % 10 == 0 or bi == len(boundaries):
            el = _t2.time() - p2t0
            eta = (len(boundaries) - bi) / (bi / el) if el > 0 else 0
            print(f"PROGRESS {100*bi/len(boundaries):5.1f}%  {bi}/{len(boundaries)}  "
                  f"確認開賽 {len(crossings)}  剩約 {int(eta//60)}分{int(eta%60)}秒", flush=True)
    # Pass3(HOLD_MODE):遞減序列回推。有些場地開賽瞬間滿值(1:30/2:00)沒被讀到
    # → Pass1 不建邊界、Pass2 沒機會驗 → 整場漏(第三場地 2-3 小時大量如此)。
    # 但計時器遞減本身讀得很清楚。故直接從粗掃 states 找「單調遞減段」,回推開賽點:
    #   開賽秒 ≈ 該遞減段第一點時間 -(round_full - 該點的計時器值)。
    # 這條只加分不減分(找到的併入 crossings),且僅 HOLD_MODE,不動已驗證影片。
    approx = set()  # Pass3 回推的點(落點粗估,標記給複審)
    if hold_mode:
        # 用「滑動窗」找遞減趨勢,容忍中間雜訊(粗掃4秒必有 None/跳動,不能要求連續嚴格遞減)。
        # 策略:掃高信心讀數,以「時間相鄰(≤step*2)且整體趨勢下降」聚成一場的遞減段。
        pts = [(t, s) for (t, s, c, st) in states
               if s is not None and c >= 0.7 and 0 < s <= round_full]
        added = 0
        i2 = 0
        n = len(pts)
        while i2 < n:
            # 從 pts[i2] 起貪婪收集「時間相鄰且值不回升太多」的點成一段
            seg = [pts[i2]]
            j = i2 + 1
            while j < n:
                pt, ps = pts[j]
                lt, ls = seg[-1]
                if pt - lt > 40:       # 時間斷太久 = 換場
                    break
                if ps <= ls + 5:       # 允許 ±5 秒 OCR 抖動,但整體要往下
                    seg.append(pts[j]); j += 1
                else:
                    break
            # 評估這段:去頭尾雜訊看趨勢(第一點 vs 最後點)
            span = seg[0][1] - seg[-1][1]
            downs = sum(1 for k in range(1, len(seg)) if seg[k][1] < seg[k-1][1])
            if len(seg) >= 4 and span >= round_full * 0.45 and downs >= 3:
                t0, s0 = seg[0]
                start = max(0, t0 - (round_full - s0))  # 回推到滿值刻(粗略,複審會調)
                if start < t0 - round_full - 5:
                    start = t0
                if not any(abs(start - c) < 40 for c in crossings):
                    crossings.append(start); approx.add(start); added += 1
            i2 = max(j, i2 + 1)
        print(f"PASS3 遞減回推: 補了 {added} 場(落點粗估,標記 approx)", flush=True)

    crossings = sorted(set(crossings))
    # HOLD_MODE:同場待命抖動可能收到多個相近 cross(如 19:52/20:12 其實同一場),
    # 相鄰 < MERGE_GAP 秒者合併取最早,讓複審清單乾淨(仍寧可多抓,只去掉同場重複)。
    if hold_mode and crossings:
        merge_gap = 25  # 只併「同場待命段內的抖動重複」(數十秒內);勿設太大以免吃掉相鄰真開賽
        merged = [crossings[0]]
        for t in crossings[1:]:
            if t - merged[-1] >= merge_gap:
                merged.append(t)
        crossings = merged
    print(f"PASS2: {len(crossings)} precise crossings (真開賽,已遞減驗證): {crossings}", flush=True)

    # approx 點(Pass3 回推)落點粗估,標記給複審重點對時間。
    def is_approx(s):
        return any(abs(s - a) < 40 for a in approx)

    # 不聚類:保留所有回合起跑點,交給「第一回合分類器」判斷哪些是真開賽。
    result = {"video": os.path.basename(video), "round_full_sec": round_full,
              "note": "全片所有計時器交界(含第1/2/3回合)。approx=True 為遞減回推、落點粗估需對時間。",
              "crossings": [{"index": i+1, "t_sec": s,
                             "timestamp": f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}",
                             "approx": is_approx(s)}
                            for i, s in enumerate(crossings)]}
    json.dump(result, open(out_json, "w"), ensure_ascii=False, indent=2)
    print(f"\nDONE: {len(crossings)} crossings ({len(approx)} 粗估) -> {out_json}", flush=True)
    for c in result["crossings"]:
        print(f"  #{c['index']:2d} {c['timestamp']}{' ~粗估' if c['approx'] else ''}", flush=True)


if __name__ == "__main__":
    main()
