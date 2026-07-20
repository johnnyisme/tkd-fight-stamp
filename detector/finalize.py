#!/usr/bin/env python3
"""
最終整合:全片交界 → 第一回合分類器過濾 → 同場重複去重 → 產出 candidates.json。

流程:
  1. 讀 crossings_full.json(所有回合起跑交界)
  2. 對每個交界裁記分板,用 round1_cls 分類「第一回合 vs 其他」
  3. 只留「第一回合」的
  4. 同場第一回合可能有多個交界(暫停重啟),相鄰 <DEDUP 秒者合併取最早
  5. 輸出 candidates.json(給網站),每個 = 一場真開賽

用法:finalize.py <crossings_json> <video> <out_candidates_json> [dedup=60]
"""
import sys, os, json, subprocess, tempfile
import cv2
from ultralytics import YOLO

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = YOLO(os.path.join(HERE, "runs", os.environ.get("SB_MODEL", "scoreboard_v1"), "weights", "best.pt"))
CLS = YOLO(os.path.join(HERE, "runs", os.environ.get("CLS_MODEL", "round1_cls"), "weights", "best.pt"))
SCALE = os.environ.get("SCALE_1080") == "1"
ROUND_FULL = int(os.environ.get("ROUND_FULL", "120"))  # 一回合秒數(找開賽瞬間)
_reader = None


def pick_board(boxes, W, H):
    """挑本場地主用台:排除貼畫面邊緣(切邊=資訊不完整=別場地/轉播台,捨棄),取 conf 最高。
    與 scan_starts.pick_board 一致,確保掃描/分類/裁圖選到同一台。"""
    m = 8
    full = [b for b in boxes if b.xyxy[0][0] > m and b.xyxy[0][1] > m
            and b.xyxy[0][2] < W - m and b.xyxy[0][3] < H - m]
    return max(full if full else list(boxes), key=lambda b: float(b.conf))


def _read_timer(video, t, tmp):
    """讀 t 秒計時器秒數(讀不到回 None)。與 crop_crossings 一致,供往前找開賽瞬間。"""
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["en"], gpu=True)
    cmd = ["ffmpeg", "-y", "-ss", str(t), "-i", video, "-frames:v", "1"]
    if SCALE:
        cmd += ["-vf", "scale=1920:1080"]
    cmd += ["-q:v", "2", tmp]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fr = cv2.imread(tmp)
    if fr is None:
        return None
    r = BOARD.predict(fr, conf=0.25, device="mps", verbose=False)[0]
    if len(r.boxes) == 0:
        return None
    H, W = fr.shape[:2]
    b = pick_board(r.boxes, W, H)
    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
    bd = fr[max(0, y1-6):min(H, y2+6), max(0, x1-6):min(W, x2+6)]
    h, w = bd.shape[:2]
    sub = bd[int(0.52*h):int(0.74*h), int(0.36*w):int(0.64*w)]
    sub = cv2.resize(sub, (sub.shape[1]*4, sub.shape[0]*4))
    best, bc = None, 0.0
    for (_, txt, conf) in _reader.readtext(sub, allowlist="0123456789:", detail=1, paragraph=False):
        s = txt.strip().replace(" ", "").replace(":", "")
        if not s.isdigit():
            continue
        val = int(s[0])*60+int(s[1:]) if len(s) == 3 else (int(s[0])*60+int(s[2:]) if len(s) == 4 else (int(s) if len(s) <= 2 else None))
        if val is not None and 0 <= val <= ROUND_FULL+2 and conf > bc:
            best, bc = val, conf
    return best


def _find_start_moment(video, t, tmp):
    """從交界 t 往前找最後滿值的下一秒 = 開賽瞬間(與 crop_crossings 一致,
    確保分類器推論輸入 = 訓練輸入,都是開賽瞬間 0:0 畫面)。"""
    last_full = None
    for dt in range(0, 21):
        val = _read_timer(video, t - dt, tmp)
        if val is not None and ROUND_FULL - 1 <= val <= ROUND_FULL + 2:
            last_full = t - dt
        elif val is not None and val < ROUND_FULL - 1 and last_full is not None:
            break
    return (last_full + 1) if last_full is not None else t + 1


HOLD_MODE = os.environ.get("HOLD_MODE") == "1"


def classify_crossing(video, t, tmp):
    """裁開賽瞬間的記分板,分類第一回合。回傳 (is_round1, conf)。
    HOLD_MODE(掃描落點可能偏晚)往前找開賽瞬間,與訓練輸入一致;
    否則(中正盃已驗證基準)維持交界後 1 秒。"""
    tt = _find_start_moment(video, t, tmp) if HOLD_MODE else (t + 1)
    cmd = ["ffmpeg", "-y", "-ss", str(tt), "-i", video, "-frames:v", "1"]
    if SCALE:
        cmd += ["-vf", "scale=1920:1080"]
    cmd += ["-q:v", "2", tmp]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fr = cv2.imread(tmp)
    if fr is None:
        return None, 0.0
    H, W = fr.shape[:2]
    r = BOARD.predict(fr, conf=0.25, device="mps", verbose=False)[0]
    if len(r.boxes) == 0:
        return None, 0.0
    b = pick_board(r.boxes, W, H)
    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
    pad = 6
    bd = fr[max(0,y1-pad):min(H,y2+pad), max(0,x1-pad):min(W,x2+pad)]
    bd = cv2.resize(bd, (600, int(600*bd.shape[0]/bd.shape[1])), interpolation=cv2.INTER_LANCZOS4)
    cv2.imwrite(tmp, bd)
    cr = CLS.predict(tmp, device="mps", verbose=False)[0]
    pred = CLS.names[cr.probs.top1]
    return (pred == "round1"), float(cr.probs.top1conf)


def main():
    crossings_json = sys.argv[1]; video = os.path.expanduser(sys.argv[2])
    out_json = sys.argv[3]; dedup = int(sys.argv[4]) if len(sys.argv) > 4 else 60
    tmp = tempfile.mktemp(suffix=".jpg")
    data = json.load(open(crossings_json))
    crossings = data["crossings"]
    total = len(crossings)
    print(f"FINALIZE 分類 {total} 個交界(第一回合過濾)", flush=True)
    r1 = []
    skip_cls = os.environ.get("SKIP_CLS") == "1"
    if skip_cls:
        # 跳過分類器:所有掃描交界都當開賽點(計分板太小/OCR不穩、分類器不可靠時)。
        # 由使用者複審刪第二三回合。conf 標 1.0 佔位。保留 approx 標記。
        print(f"FINALIZE 跳過第一回合分類(SKIP_CLS),{total} 個交界全保留", flush=True)
        r1 = [(c["t_sec"], 1.0, bool(c.get("approx"))) for c in crossings]
        print(f"PROGRESS 100.0%  {total}/{total}  跳過分類,全保留 {total}", flush=True)
    else:
        import time as _time
        t0 = _time.time()
        for i, c in enumerate(crossings, 1):
            t = c["t_sec"]
            is_r1, conf = classify_crossing(video, t, tmp)
            tag = "round1" if is_r1 else ("other" if is_r1 is not None else "no-board")
            print(f"  {t//3600:02d}:{t%3600//60:02d}:{t%60:02d}  {tag}({conf:.2f})", flush=True)
            if is_r1:
                r1.append((t, conf, bool(c.get("approx"))))
            if i % 10 == 0 or i == total:
                el = _time.time() - t0
                eta = (total - i) / (i / el) if el > 0 else 0
                print(f"PROGRESS {100*i/total:5.1f}%  {i}/{total}  "
                      f"第一回合累計 {len(r1)}  剩約 {int(eta//60)}分{int(eta%60)}秒", flush=True)
    # 同場重複去重:相鄰 < dedup 秒,取最早
    r1.sort()
    merged = []
    for t, conf, ap in r1:
        if merged and t - merged[-1][0] < dedup:
            continue  # 同場第一回合的重複交界,已有更早的
        merged.append((t, conf, ap))

    # 統一往前 2 秒(寧早勿晚):交界 OCR 常落在 1:58,-2 秒回到開賽瞬間、不會晚。
    OFFSET = 2
    cands = [{"index": i+1, "t_sec": max(0, t - OFFSET),
              "timestamp": f"{max(0,t-OFFSET)//3600:02d}:{max(0,t-OFFSET)%3600//60:02d}:{max(0,t-OFFSET)%60:02d}",
              "cls_conf": round(conf, 2), "method": "round1_start", "approx": ap}
             for i, (t, conf, ap) in enumerate(merged)]
    out = {"video": data.get("video"), "youtube_url": "https://www.youtube.com/watch?v=X-HcwuHepFU",
           "note": "計時器交界 → 第一回合分類器過濾 → 同場去重。approx=True 為遞減回推、落點粗估需對時間。",
           "candidates": cands}
    json.dump(out, open(out_json, "w"), ensure_ascii=False, indent=2)
    print(f"\nDONE: {len(data['crossings'])} 交界 → {len(r1)} 第一回合 → 去重後 {len(merged)} 場 -> {out_json}", flush=True)
    for c in cands:
        print(f"  #{c['index']:2d} {c['timestamp']} (cls {c['cls_conf']}){' ~粗估' if c['approx'] else ''}", flush=True)


if __name__ == "__main__":
    main()
