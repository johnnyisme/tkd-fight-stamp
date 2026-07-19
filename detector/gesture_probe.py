#!/usr/bin/env python3
"""
開賽精確定位 v3：用「裁判退場那一刻」當開賽錨點。

開賽流程觀察：裁判喊 시작 → 選手開打 → 裁判從兩選手中間快速退向場邊。
所以開賽 ≈ 裁判「開始持續遠離場地中央」的轉折點。

追蹤：每幀用 referee_v1 定位裁判 bbox，記錄其中心 x。
以窗口內裁判 x 的中位數為「場地中央基準」，看裁判何時開始持續偏離。
轉折點 = 裁判 x 從「靠近基準」變為「持續遠離」的時刻。

用法：gesture_probe.py <video> <center_sec> <half_window_sec> <out_prefix>
"""
import sys, os, subprocess, tempfile, json
import cv2
import numpy as np
from ultralytics import YOLO

HERE = os.path.dirname(os.path.abspath(__file__))
REF = YOLO(os.path.join(HERE, "runs", "referee_v1", "weights", "best.pt"))


def ref_center_x(video, t, tmp):
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", video, "-frames:v", "1",
                    "-q:v", "2", tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frame = cv2.imread(tmp)
    if frame is None:
        return None, 0.0, None
    W = frame.shape[1]
    r = REF.predict(frame, conf=0.35, device="mps", verbose=False)[0]
    if len(r.boxes) == 0:
        return None, 0.0, W
    b = max(r.boxes, key=lambda b: float(b.conf))
    x1, _, x2, _ = b.xyxy[0].tolist()
    cx = (x1 + x2) / 2
    return cx / W, float(b.conf), W  # 正規化 0..1


def main():
    video = sys.argv[1]; center = int(sys.argv[2]); half = int(sys.argv[3]); out_prefix = sys.argv[4]
    tmp = tempfile.mktemp(suffix=".jpg")

    series = []
    for t in range(center - half, center + half + 1):
        cx, conf, W = ref_center_x(video, t, tmp)
        series.append((t, cx, conf))
        cs = f"{cx:.3f}" if cx is not None else "--"
        print(f"t={t}  ref_cx={cs}  conf={conf:.2f}", flush=True)

    # 基準：窗口前段(待命期)裁判 x 的中位數 = 場地中央
    valid = [(t, cx) for (t, cx, c) in series if cx is not None and c >= 0.4]
    xs = [cx for (_, cx) in valid]
    base = float(np.median(xs)) if xs else 0.5
    # 偏離量
    dev = [(t, abs(cx - base)) for (t, cx) in valid]
    # 轉折點:偏離量首次超過門檻(0.06,約畫面6%寬)且之後持續 → 開賽
    THRESH = 0.06
    start_t = None
    for i, (t, d) in enumerate(dev):
        if d >= THRESH:
            # 確認之後 3 個有效點也大多偏離(避免瞬時抖動)
            after = [dd for (_, dd) in dev[i:i+4]]
            if sum(1 for a in after if a >= THRESH) >= max(2, len(after) - 1):
                start_t = t
                break

    with open(out_prefix + ".json", "w") as f:
        json.dump({"center": center, "base_x": base, "threshold": THRESH,
                   "detected_start_sec": start_t,
                   "series": [{"t": t, "cx": (None if cx is None else float(cx)), "conf": float(c)}
                              for (t, cx, c) in series]}, f, indent=2)

    # 畫圖
    ts = [s[0] for s in series]
    cxs = [s[1] if s[1] is not None else np.nan for s in series]
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.figure(figsize=(12, 4))
    plt.axhline(base, color="gray", lw=0.8, label=f"center base={base:.3f}")
    plt.axhline(base + THRESH, color="orange", lw=0.6, ls="--")
    plt.axhline(base - THRESH, color="orange", lw=0.6, ls="--")
    plt.plot(ts, cxs, "-o", ms=3)
    if start_t is not None:
        plt.axvline(start_t, color="red", lw=1.5, label=f"detected start={start_t}s")
    plt.xlabel("time (s)"); plt.ylabel("referee center x (normalized)")
    plt.title(f"referee horizontal position @ candidate {center}s")
    plt.legend(); plt.grid(alpha=0.3); plt.tight_layout()
    plt.savefig(out_prefix + ".png", dpi=90)
    print(f"\ndetected_start = {start_t}s  (base_x={base:.3f})", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
