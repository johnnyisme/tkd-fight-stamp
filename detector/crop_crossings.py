#!/usr/bin/env python3
"""
把全片交界點的記分板裁出來(供第一回合分類標記/訓練)。
每個交界點取數幀(交界前後),用記分板偵測器裁出記分板放大存檔。
輸出檔名 x{idx:03d}_t{t}.jpg，方便對應。
"""
import sys, os, json, subprocess, tempfile
import cv2
from ultralytics import YOLO
import easyocr

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = YOLO(os.path.join(HERE, "runs", os.environ.get("SB_MODEL", "scoreboard_v1"), "weights", "best.pt"))
ROUND_FULL = int(os.environ.get("ROUND_FULL", "120"))  # 一回合秒數(找開賽瞬間用)
_reader = None


def reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en"], gpu=True)
    return _reader


def read_timer(video, t, tmp, scale):
    """讀 t 秒的計時器秒數(讀不到回 None)。用來往前找開賽瞬間。"""
    cmd = ["ffmpeg", "-y", "-ss", str(t), "-i", video, "-frames:v", "1"]
    if scale:
        cmd += ["-vf", "scale=1920:1080"]
    cmd += ["-q:v", "2", tmp]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fr = cv2.imread(tmp)
    if fr is None:
        return None
    r = BOARD.predict(fr, conf=0.25, device="mps", verbose=False)[0]
    if len(r.boxes) == 0:
        return None
    b = max(r.boxes, key=lambda b: float(b.conf))
    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
    H, W = fr.shape[:2]
    bd = fr[max(0, y1-6):min(H, y2+6), max(0, x1-6):min(W, x2+6)]
    h, w = bd.shape[:2]
    sub = bd[int(0.52*h):int(0.74*h), int(0.36*w):int(0.64*w)]
    sub = cv2.resize(sub, (sub.shape[1]*4, sub.shape[0]*4))
    best, bc = None, 0.0
    for (_, txt, conf) in reader().readtext(sub, allowlist="0123456789:", detail=1, paragraph=False):
        s = txt.strip().replace(" ", "").replace(":", "")
        if not s.isdigit():
            continue
        val = int(s[0])*60 + int(s[1:]) if len(s) == 3 else (int(s[0])*60+int(s[2:]) if len(s) == 4 else (int(s) if len(s) <= 2 else None))
        if val is not None and 0 <= val <= ROUND_FULL+2 and conf > bc:
            best, bc = val, conf
    return best


def find_start_moment(video, t, tmp, scale):
    """從交界點 t 往前找「最後一個滿值(2:00)」的下一秒 = 真開賽瞬間。
    二號場地掃描落點常偏晚數秒,直接用 t+1 會裁到比賽中(比分已變)。
    往前掃最多 20 秒,找到最後仍是滿值的點,回其下一秒(比分最接近 0:0、ROUND 最明確)。"""
    full = ROUND_FULL
    last_full = None
    for dt in range(0, 21):
        val = read_timer(video, t - dt, tmp, scale)
        if val is not None and full - 1 <= val <= full + 2:
            last_full = t - dt  # 往前仍是滿值 → 持續更新(要最早的滿值段起點附近)
        elif val is not None and val < full - 1 and last_full is not None:
            break  # 已離開滿值又往前遇到跑動,停(避免跨到上一場)
    return (last_full + 1) if last_full is not None else t + 1


def main():
    crossings_json = sys.argv[1]
    out_dir = sys.argv[2]
    # 影片路徑可由第3參數指定(預設國選那支)
    video = os.path.expanduser(sys.argv[3]) if len(sys.argv) > 3 else os.path.expanduser("~/Downloads/tkd_video/venue1_am.mkv")
    scale = os.environ.get("SCALE_1080") == "1"
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mktemp(suffix=".jpg")
    data = json.load(open(crossings_json))
    crossings = data["crossings"]
    n = 0
    for c in crossings:
        idx, t = c["index"], c["t_sec"]
        # 往前找真開賽瞬間再裁(掃描落點可能偏晚數秒,直接 t+1 會裁到比賽中)。
        # 開賽瞬間 ROUND 格最明確、比分最接近 0:0,標記與訓練都最乾淨。
        tt = find_start_moment(video, t, tmp, scale)
        cmd = ["ffmpeg", "-y", "-ss", str(tt), "-i", video, "-frames:v", "1"]
        if scale:
            cmd += ["-vf", "scale=1920:1080"]
        cmd += ["-q:v", "2", tmp]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fr = cv2.imread(tmp)
        if fr is None:
            continue
        H, W = fr.shape[:2]
        r = BOARD.predict(fr, conf=0.25, device="mps", verbose=False)[0]
        if len(r.boxes) == 0:
            continue
        b = max(r.boxes, key=lambda b: float(b.conf))
        x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
        pad = 6
        bd = fr[max(0,y1-pad):min(H,y2+pad), max(0,x1-pad):min(W,x2+pad)]
        bd = cv2.resize(bd, (600, int(600*bd.shape[0]/bd.shape[1])), interpolation=cv2.INTER_LANCZOS4)
        cv2.imwrite(f"{out_dir}/x{idx:03d}_t{t}.jpg", bd)
        n += 1
    print(f"cropped {n}/{len(crossings)} crossing scoreboards -> {out_dir}", flush=True)


if __name__ == "__main__":
    main()
