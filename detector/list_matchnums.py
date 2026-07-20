#!/usr/bin/env python3
"""
對每個第一回合開賽點,盡量讀 MATCH 場次編號(讀到填、讀不到留空),
輸出簡潔清單「時間戳 <TAB> 場次」供 user 手動複審。

不管 OCR 品質 —— 讀到什麼填什麼,user 會複審修正。

用法:list_matchnums.py <candidates_json> <video> <out_txt>
環境:SB_MODEL(記分板模型)、SCALE_1080=1(4K縮放)
"""
import sys, os, json, subprocess, tempfile, collections
import cv2
from ultralytics import YOLO
import easyocr

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = YOLO(os.path.join(HERE, "runs", os.environ.get("SB_MODEL", "scoreboard_sb2"), "weights", "best.pt"))
SCALE = os.environ.get("SCALE_1080") == "1"
_reader = None
_tmp = tempfile.mktemp(suffix=".jpg")


def reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en"], gpu=True)
    return _reader


def enhance(img):
    up = cv2.resize(img, (img.shape[1]*6, img.shape[0]*6), interpolation=cv2.INTER_CUBIC)
    g = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    g = cv2.fastNlMeansDenoising(g, None, 10, 7, 21)
    g = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(g)
    blur = cv2.GaussianBlur(g, (0, 0), 3)
    return cv2.addWeighted(g, 1.6, blur, -0.6, 0)


def read_matchnum_1frame(video, t):
    cmd = ["ffmpeg", "-y", "-ss", str(t), "-i", video, "-frames:v", "1"]
    if SCALE:
        cmd += ["-vf", "scale=1920:1080"]
    cmd += ["-q:v", "2", _tmp]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fr = cv2.imread(_tmp)
    if fr is None:
        return None
    r = BOARD.predict(fr, conf=0.25, device="mps", verbose=False)[0]
    if len(r.boxes) == 0:
        return None
    b = max(r.boxes, key=lambda b: float(b.conf))
    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
    H, W = fr.shape[:2]; pad = 6
    bd = fr[max(0,y1-pad):min(H,y2+pad), max(0,x1-pad):min(W,x2+pad)]
    h, w = bd.shape[:2]
    # MATCH 編號在中間欄上方,範圍放寬
    sub = bd[int(0.28*h):int(0.46*h), int(0.40*w):int(0.66*w)]
    if sub.size == 0:
        return None
    res = reader().readtext(enhance(sub), allowlist="0123456789", detail=1, paragraph=False)
    best, bc = None, 0
    for (_, txt, c) in res:
        if len(txt) == 3 and c > bc:   # 場次號多為 3 位
            best, bc = txt, c
    return best


def vote_matchnum(video, ct):
    """開賽後多幀投票讀場次號。回傳 (最可能場次號 or '', 票數統計)。"""
    votes = collections.Counter()
    for t in range(ct + 1, ct + 15, 2):
        n = read_matchnum_1frame(video, t)
        if n:
            votes[n] += 1
    if not votes:
        return "", {}
    return votes.most_common(1)[0][0], dict(votes)


def main():
    cand_json = sys.argv[1]; video = os.path.expanduser(sys.argv[2]); out_txt = sys.argv[3]
    reader()
    data = json.load(open(cand_json))
    cands = data["candidates"]
    total = len(cands)
    print(f"OCR 場次號 {total} 場", flush=True)
    lines = []
    import time as _time
    t0 = _time.time()
    for i, c in enumerate(cands, 1):
        ct = c["t_sec"]
        num, votes = vote_matchnum(video, ct)
        ts = c["timestamp"]
        # approx(遞減回推)落點粗估,時間戳後緊黏「~」提示複審重點對時間
        # (黏緊不加空格,才不會被下游 split 當成場次號)
        mark = "~" if c.get("approx") else ""
        lines.append(f"{ts}{mark}\t{num}")
        print(f"{ts}{mark}\t{num or '(讀不到)'}\t投票={votes}", flush=True)
        if i % 5 == 0 or i == total:
            el = _time.time() - t0
            eta = (total - i) / (i / el) if el > 0 else 0
            got = sum(1 for l in lines if l.split("\t")[1])
            print(f"PROGRESS {100*i/total:5.1f}%  {i}/{total}  "
                  f"讀到場次 {got}  剩約 {int(eta//60)}分{int(eta%60)}秒", flush=True)
    with open(out_txt, "w") as f:
        f.write("時間戳\t場次\n")
        f.write("\n".join(lines) + "\n")
    read = sum(1 for l in lines if l.split("\t")[1])
    print(f"\nDONE: {len(lines)} 場, OCR 讀到場次 {read} 場, 留空 {len(lines)-read} 場 -> {out_txt}", flush=True)


if __name__ == "__main__":
    main()
