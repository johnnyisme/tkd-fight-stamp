#!/usr/bin/env python3
"""
兩階段掃描：偵測每一場/回合的開賽點（計時器從 2:00 開始倒數那一刻）。

開賽的精確時刻 = 計時器「持續 2:00（待命）→ 首次 < 2:00（開打）」的下降沿。
這對應主審喊「시작」、選手開打的瞬間，也就是要標到 YouTube 的錨點。

Pass 1（粗排，步長 coarse=15s）：讀計時器，找「timer==2:00」的取樣點。
Pass 2（細確，步長 1s）：對每個 2:00 點之後的區間，1 秒取樣，定位首次 < 2:00 的時刻。

去重：同一場的多個 2:00 取樣點會聚成一個下降沿；相鄰 < 一回合長度(150s)者合併。

賽制回合秒數（round_full）由使用者於開跑前指定：2:00→120、1:30→90。
同一支影片賽制固定，不會混用，故不自動偵測滿值。

用法：scan.py <video> <out_json> <round_full_sec> [start] [end] [coarse]
"""
import sys, os, json, subprocess, tempfile
import cv2
from ultralytics import YOLO
import easyocr

HERE = os.path.dirname(os.path.abspath(__file__))
WEIGHTS = os.path.join(HERE, "runs", "scoreboard_v1", "weights", "best.pt")

_model = None
_reader = None
_tmp = tempfile.mktemp(suffix=".jpg")


def engines():
    global _model, _reader
    if _model is None:
        _model = YOLO(WEIGHTS)
        _reader = easyocr.Reader(["en"], gpu=True)
    return _model, _reader


def parse_timer(text):
    t = text.strip().replace(" ", "")
    if ":" in t:
        p = t.split(":")
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit() and int(p[1]) < 60:
            return int(p[0]) * 60 + int(p[1])
        return None
    if t.isdigit():
        if len(t) == 3:
            v = int(t[0]) * 60 + int(t[1:])
            return v if v <= 130 else None
        if len(t) == 4 and t[0] == "0":
            v = int(t[1]) * 60 + int(t[2:])
            return v if v <= 130 else None
    return None


def timer_at(video, t):
    """回傳 (timer_sec, conf)；讀不到回 (None, 0)。"""
    model, reader = engines()
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", video, "-frames:v", "1",
                    "-q:v", "2", _tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frame = cv2.imread(_tmp)
    if frame is None:
        return None, 0.0
    r = model.predict(frame, conf=0.25, device="mps", verbose=False)[0]
    if len(r.boxes) == 0:
        return None, 0.0
    b = max(r.boxes, key=lambda b: float(b.conf))
    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
    H, W = frame.shape[:2]; pad = 6
    board = frame[max(0,y1-pad):min(H,y2+pad), max(0,x1-pad):min(W,x2+pad)]
    h, w = board.shape[:2]
    sub = board[int(0.52*h):int(0.74*h), int(0.36*w):int(0.64*w)]
    if sub.size == 0:
        return None, 0.0
    sub = cv2.resize(sub, (sub.shape[1]*4, sub.shape[0]*4), interpolation=cv2.INTER_CUBIC)
    res = reader.readtext(sub, allowlist="0123456789:", detail=1, paragraph=False)
    best, bc = None, 0.0
    for (_, txt, conf) in res:
        sec = parse_timer(txt)
        if sec is not None and conf > bc:
            best, bc = sec, conf
    return best, bc


def main():
    video = sys.argv[1]; out_json = sys.argv[2]
    round_full = int(sys.argv[3])  # 賽制回合秒數: 2:00->120, 1:30->90
    start = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    end = int(sys.argv[5]) if len(sys.argv) > 5 else None
    coarse = int(sys.argv[6]) if len(sys.argv) > 6 else 15
    if end is None:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", video],
                           stdout=subprocess.PIPE, text=True)
        end = int(float(r.stdout.strip()))

    # 待命值容許範圍：滿值 ± 3s；下降沿門檻：< 滿值-4s
    ready_lo, ready_hi = round_full - 3, round_full + 3
    drop_below = round_full - 4
    fmt = lambda s: f"{s//60}:{s%60:02d}"

    engines()
    print(f"round_full={round_full}s ({fmt(round_full)})  PASS1 coarse {start}..{end}s step={coarse}s", flush=True)
    # Pass 1: 找 timer≈滿值 (待命) 且 conf>=0.5 的取樣點；順手濾掉 > 滿值+容許 的 OCR 錯讀
    ready_points = []
    t = start
    while t <= end:
        sec, conf = timer_at(video, t)
        if sec is not None and conf >= 0.5 and ready_lo <= sec <= ready_hi:
            ready_points.append(t)
            print(f"  ready({fmt(round_full)}) @ {t}s conf={conf:.2f}", flush=True)
        t += coarse

    # 把連續的 ready 點聚成「待命區段」：相鄰 <= 5*coarse 視為同段
    # (放寬,因待命期長達數分鐘且中間 OCR 可能漏讀幾個點；一場最短間隔由後面 150s 去重把關)
    segments = []
    for tp in ready_points:
        if segments and tp - segments[-1][-1] <= 5 * coarse:
            segments[-1].append(tp)
        else:
            segments.append([tp])
    print(f"PASS1 done: {len(ready_points)} ready pts -> {len(segments)} segments", flush=True)

    # Pass 2: 對每段結尾之後，1s 取樣找首次 < (滿值-4) 的時刻 = 開賽
    print("PASS2 fine scan at segment tails", flush=True)
    starts = []
    for seg in segments:
        tail = seg[-1]
        found = None
        for tt in range(tail, min(end, tail + coarse + 5) + 1):
            sec, conf = timer_at(video, tt)
            if sec is not None and conf >= 0.5 and sec < drop_below:
                found = tt
                break
        start_pt = found if found is not None else tail
        # 去重:與前一個開賽點若 < 150s (一回合)，視為同場回合抖動，跳過
        if starts and start_pt - starts[-1] < 150:
            continue
        starts.append(start_pt)
        print(f"  start @ {start_pt}s ({start_pt//60}:{start_pt%60:02d})", flush=True)

    result = {
        "video": os.path.basename(video),
        "round_full_sec": round_full,
        "scan_range": [start, end], "coarse_step": coarse,
        "candidate_starts_sec": starts,
        "candidate_starts_hms": [f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}" for s in starts],
    }
    with open(out_json, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nDONE: {len(starts)} candidate starts -> {out_json}", flush=True)


if __name__ == "__main__":
    main()
