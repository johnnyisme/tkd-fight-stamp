#!/usr/bin/env python3
"""
回溯校準法：把計時器粗候選精修到「計時器 2:00→1:59 交界」(真開賽起跑線)。

user 的 pattern：
  計時器讀到的值代表「已經跑掉多少秒」。從候選點讀到 1:50 → 已跑 10 秒 →
  往前 10 秒應該接近 2:00。若仍非 2:00(中間有暫停/等待)→ 繼續往前找，
  直到定位「計時器仍是 2:00、下一秒變 1:59」的那個交界 = 起跑線。
  手勢就在起跑線前幾秒（交界往前 3 秒當錨點；差幾秒無所謂）。

流程（每個候選 ct）：
  1. 讀 ct 的計時器 sec；估算起跑線 ≈ ct - (ROUND_FULL - sec)
  2. 在估算點附近 ±窗口 1 秒逐秒讀，找「timer==ROUND_FULL 且下一秒<ROUND_FULL」的交界
  3. 若估算點還沒到 2:00（讀值<ROUND_FULL），再往前跳，處理暫停
  4. 錨點 = 交界秒 - 3

用法：refine_by_timer.py <video> <candidates_json> <out_json> <round_full>
"""
import sys, os, json, subprocess, tempfile
import cv2
from ultralytics import YOLO
import easyocr

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = YOLO(os.path.join(HERE, "runs", "scoreboard_v1", "weights", "best.pt"))
_reader = None
_tmp = tempfile.mktemp(suffix=".jpg")


def reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en"], gpu=True)
    return _reader


def parse_timer(text):
    t = text.strip().replace(" ", "")
    if ":" in t:
        p = t.split(":")
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit() and int(p[1]) < 60:
            return int(p[0]) * 60 + int(p[1])
        return None
    if t.isdigit() and len(t) == 3:
        v = int(t[0]) * 60 + int(t[1:])
        return v if v <= 130 else None
    return None


def timer_at(video, t):
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", video, "-frames:v", "1",
                    "-q:v", "2", _tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frame = cv2.imread(_tmp)
    if frame is None:
        return None, 0.0
    r = BOARD.predict(frame, conf=0.25, device="mps", verbose=False)[0]
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
    res = reader().readtext(sub, allowlist="0123456789:", detail=1, paragraph=False)
    best, bc = None, 0.0
    for (_, txt, conf) in res:
        sec = parse_timer(txt)
        if sec is not None and conf > bc:
            best, bc = sec, conf
    return best, bc


def find_startline(video, ct, round_full):
    """
    找 2:00→1:59 交界(起跑線 = 最後一個滿值、其後開始遞減的時刻)。
    候選點可能落在「待命期(仍2:00)」→ 交界在其後；也可能落在「已跑」→ 交界在其前。
    故雙向掃描：以 ct 為中心，掃 [ct - back, ct + fwd] 每秒計時器，全域找交界。
    """
    log = []
    sec, conf = timer_at(video, ct)
    log.append(f"ct={ct} timer={sec} conf={conf:.2f}")
    if sec is None:
        return None, log
    # 掃描窗口：往前涵蓋「已跑掉的秒數 + buffer」，往後涵蓋「待命期可能還沒開跑」
    back = (round_full - sec) + 25 if sec is not None else round_full
    fwd = 25
    lo = max(0, ct - back)
    hi = ct + fwd
    vals = {}
    for t in range(lo, hi + 1):
        s, c = timer_at(video, t)
        vals[t] = (s, c)
    # 找所有「滿值(待命)」時刻，取「最後一個滿值、其後 8 秒內出現遞減」的作為起跑線
    startline = None
    for t in range(hi, lo - 1, -1):  # 由後往前找最後一個合格交界
        s, c = vals[t]
        if s is None or c < 0.5 or abs(s - round_full) > 1:
            continue
        after = [vals[k][0] for k in range(t + 1, min(hi, t + 9) + 1)
                 if vals[k][0] is not None and vals[k][1] >= 0.5]
        if any(a is not None and a < round_full - 1 for a in after):
            startline = t
            break
    ready_ts = [t for t in range(lo, hi + 1)
                if vals[t][0] is not None and vals[t][1] >= 0.5 and abs(vals[t][0] - round_full) <= 1]
    log.append(f"scanned {lo}..{hi} ready_pts={ready_ts[-6:] if ready_ts else []} -> startline={startline}")
    return startline, log


def main():
    video = sys.argv[1]; cand_json = sys.argv[2]; out_json = sys.argv[3]; round_full = int(sys.argv[4])
    data = json.load(open(cand_json))
    reader()
    results = []
    for c in data["candidates"]:
        ct = c["t_sec"]
        sl, log = find_startline(video, ct, round_full)
        for ln in log:
            print(f"  #{c['index']:2d} {ln}", flush=True)
        if sl is not None:
            anchor = sl - 3
            r = {"index": c["index"], "t_sec": anchor,
                 "timestamp": f"{anchor//3600:02d}:{anchor%3600//60:02d}:{anchor%60:02d}",
                 "startline_sec": sl, "timer_cand_sec": ct, "method": "startline",
                 "confident": True}
        else:
            # 誠實回報:找不到明確 2:00→1:59 交界。可能非真比賽/待命過長/OCR不穩。
            # 仍給計時器候選當「大概去這附近找」的提示,但明確標記需人工判斷。
            r = {"index": c["index"], "t_sec": ct,
                 "timestamp": f"{ct//3600:02d}:{ct%3600//60:02d}:{ct%60:02d}",
                 "startline_sec": None, "timer_cand_sec": ct, "method": "NOT_FOUND",
                 "confident": False,
                 "note": "AI 找不到明確開賽起跑線(可能非真比賽/待命過長)。時間僅為計時器候選,請人工判斷。"}
        print(f"#{c['index']:2d} ct={ct} -> {r['method']} anchor={r['t_sec']}", flush=True)
        results.append(r)
    ns = sum(1 for r in results if r["confident"])
    out = {"video": data["video"], "youtube_url": data.get("youtube_url"),
           "round_full_sec": round_full,
           "note": f"回溯校準:{ns}/{len(results)} 場精準定位到計時器 2:00→1:59 交界(開賽)。"
                   f"其餘標 NOT_FOUND 者 AI 定位不到,請人工判斷(可能非真比賽)。",
           "candidates": results}
    json.dump(out, open(out_json, "w"), ensure_ascii=False, indent=2)
    print(f"\nDONE: {ns}/{len(results)} confident startline; {len(results)-ns} NOT_FOUND (需人工判斷) -> {out_json}", flush=True)


if __name__ == "__main__":
    main()
