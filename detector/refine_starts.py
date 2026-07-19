#!/usr/bin/env python3
"""
把計時器粗候選精修到「裁判準備手勢起始」那一刻（開賽錨點）。

錨點定義（依 user 選定 t=248-249 型）：
  裁判「雙手都穩定偵測到 + 雙手水平距離小(靠近，準備姿勢) + 之後距離會放大」
  的起始點 —— 即「手剛伸出來準備」那一刻，在計時器開跑前數秒（寧早勿晚）。

流程（每個粗候選）：
  在 [cand-HALF, cand+HALF] 窗口逐秒：
    1. referee_v1 定位裁判，框內跑 pose 取雙手腕
    2. 記錄 both_conf（雙手皆偵測到）與 hand_dist（正規化）
  找錨點：
    - 掃描窗口，找一個時刻 a，使得
        * a 起連續 >=2 秒 both_conf 高且 hand_dist 小 (<=NEAR)
        * 其後數秒內 hand_dist 出現放大 (>=WIDE)  ← 確認是「準備→展開」而非靜止
    - 取最早符合者為錨點（寧早勿晚）
  找不到就退回計時器候選（fallback），並標記 method。

用法：refine_starts.py <video> <candidates_json> <out_json> [half=18]
"""
import sys, os, json, subprocess, tempfile
import numpy as np
from ultralytics import YOLO
import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
REF = YOLO(os.path.join(HERE, "runs", "referee_v1", "weights", "best.pt"))
POSE = YOLO("yolo11n-pose.pt")
L_WR, R_WR = 9, 10
NEAR = 0.10   # 雙手靠近門檻(正規化框寬)
WIDE = 0.18   # 雙手展開門檻


def measure(video, t, tmp):
    """回傳 (both_conf, hand_dist) ；抓不到回 (0, None)。"""
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", video, "-frames:v", "1",
                    "-q:v", "2", tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frame = cv2.imread(tmp)
    if frame is None:
        return 0.0, None
    H, W = frame.shape[:2]
    r = REF.predict(frame, conf=0.35, device="mps", verbose=False)[0]
    if len(r.boxes) == 0:
        return 0.0, None
    b = max(r.boxes, key=lambda b: float(b.conf))
    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
    ex = int(0.4 * (x2 - x1)); ey = int(0.3 * (y2 - y1))
    crop = frame[max(0, y1-ey):min(H, y2+ey), max(0, x1-ex):min(W, x2+ex)]
    bw = crop.shape[1]
    if bw == 0:
        return 0.0, None
    pr = POSE.predict(crop, conf=0.25, device="mps", verbose=False)[0]
    if pr.keypoints is None or len(pr.keypoints.data) == 0:
        return 0.0, None
    idx = 0
    if pr.boxes is not None and len(pr.boxes) > 1:
        idx = int(np.argmax([(pb.xywh[0][2]*pb.xywh[0][3]).item() for pb in pr.boxes]))
    kp = pr.keypoints.data[idx].cpu().numpy()
    lwr, rwr = kp[L_WR], kp[R_WR]
    both = min(lwr[2], rwr[2])
    dist = abs(lwr[0] - rwr[0]) / bw if both > 0.3 else None
    return float(both), (None if dist is None else float(dist))


def find_anchor(samples):
    """samples: list of (t, both_conf, dist)。回傳錨點 t 或 None。"""
    n = len(samples)
    for i in range(n):
        t, bc, d = samples[i]
        if bc < 0.5 or d is None or d > NEAR:
            continue
        # 條件1: a 起連續 >=2 秒 靠近
        near_run = 0
        for j in range(i, min(n, i + 3)):
            if samples[j][2] is not None and samples[j][1] >= 0.5 and samples[j][2] <= NEAR:
                near_run += 1
            else:
                break
        if near_run < 2:
            continue
        # 條件2: 其後 6 秒內出現展開
        widened = any(samples[k][2] is not None and samples[k][2] >= WIDE
                      for k in range(i, min(n, i + 7)))
        if widened:
            return t
    return None


def main():
    video = sys.argv[1]; cand_json = sys.argv[2]; out_json = sys.argv[3]
    half = int(sys.argv[4]) if len(sys.argv) > 4 else 18
    tmp = tempfile.mktemp(suffix=".jpg")
    data = json.load(open(cand_json))
    cands = data["candidates"]
    results = []
    for c in cands:
        ct = c["t_sec"]
        samples = []
        for t in range(ct - half, ct + 6):  # 手勢在計時器候選之前，往前窗大、往後小
            bc, d = measure(video, t, tmp)
            samples.append((t, bc, d))
        anchor = find_anchor(samples)
        method = "gesture" if anchor is not None else "timer_fallback"
        final_t = anchor if anchor is not None else ct
        results.append({"index": c["index"], "timer_cand_sec": ct,
                        "refined_sec": final_t, "method": method,
                        "refined_hms": f"{final_t//3600:02d}:{final_t%3600//60:02d}:{final_t%60:02d}"})
        print(f"#{c['index']:2d} timer={ct}s -> refined={final_t}s [{method}]", flush=True)

    out = {"video": data["video"], "youtube_url": data.get("youtube_url"),
           "note": "計時器粗候選經『裁判準備手勢』精修。method=gesture 表示抓到手勢起始;timer_fallback 表示退回計時器點。",
           "candidates": [{"index": r["index"], "t_sec": r["refined_sec"],
                           "timestamp": r["refined_hms"], "method": r["method"],
                           "timer_cand_sec": r["timer_cand_sec"]} for r in results]}
    json.dump(out, open(out_json, "w"), ensure_ascii=False, indent=2)
    ng = sum(1 for r in results if r["method"] == "gesture")
    print(f"\nDONE: {ng}/{len(results)} refined by gesture -> {out_json}", flush=True)


if __name__ == "__main__":
    main()
