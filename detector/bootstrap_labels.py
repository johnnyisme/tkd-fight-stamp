#!/usr/bin/env python3
"""
半自動標記器：用弱偵測（顏色+亮數字）提議記分板框，供人工一次驗證。

不追求生產級穩定，只求在這批訓練幀上提議得夠準，人工挑掉錯的即可。
記分板紅半邊的判別特徵：
  - 高飽和紅塊，面積適中、略方
  - 塊內含「大的亮色數字」（比分）——用塊內高亮像素佔比判別，排除地墊/桌布純色紅
  - 右側鄰接較暗區（計時器欄）

提議框 = 紅塊往右延伸 ~2.4 倍寬（涵蓋計時器欄 + 藍半邊），上下各留少量 padding。
輸出：每張的提議框座標 + 一張縮圖總覽(montage) 供人工核對。
"""
import sys, os, glob
import cv2
import numpy as np


def red_mask(hsv):
    m1 = cv2.inRange(hsv, np.array((0, 110, 80)), np.array((10, 255, 255)))
    m2 = cv2.inRange(hsv, np.array((168, 110, 80)), np.array((180, 255, 255)))
    return cv2.bitwise_or(m1, m2)


def propose(frame):
    h, w = frame.shape[:2]
    area = h * w
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    m = red_mask(hsv)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best, best_score = None, 0.0
    for c in cs:
        x, y, bw, bh = cv2.boundingRect(c)
        a = bw * bh
        if a < 0.0008 * area or a > 0.06 * area:
            continue
        ar = bw / max(bh, 1)
        if ar < 0.5 or ar > 2.2:
            continue
        fill = cv2.contourArea(c) / max(a, 1)
        if fill < 0.6:
            continue
        # 塊內是否有大的亮數字：取塊內灰階，高亮像素(>180)佔比在合理區間
        roi = gray[y:y+bh, x:x+bw]
        if roi.size == 0:
            continue
        bright_frac = float((roi > 180).mean())
        if bright_frac < 0.04 or bright_frac > 0.5:
            continue
        # 右鄰是否有較暗區（計時器欄）：看右側緊鄰一小條的平均亮度
        gx0 = x + bw
        gx1 = min(w, x + bw + max(6, bw // 4))
        strip = gray[y:y+bh, gx0:gx1]
        dark_bonus = 1.0
        if strip.size > 0 and strip.mean() < 110:
            dark_bonus = 1.6
        score = a * bright_frac * dark_bonus
        if score > best_score:
            best_score = score
            best = (x, y, bw, bh)

    if best is None:
        return None
    x, y, bw, bh = best
    # 延伸成完整記分板：紅塊寬 ~= 單邊，全板約 2.4x 寬；上下各 pad 12%
    pad_y = int(0.15 * bh)
    x0 = max(0, x - int(0.05 * bw))
    y0 = max(0, y - pad_y)
    x1 = min(w, x + int(2.5 * bw))
    y1 = min(h, y + bh + pad_y)
    return (x0, y0, x1 - x0, y1 - y0)


def main(img_glob, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    paths = sorted(p for p in glob.glob(img_glob) if ".annot" not in p)
    results = {}
    thumbs = []
    for p in paths:
        frame = cv2.imread(p)
        if frame is None:
            continue
        box = propose(frame)
        name = os.path.basename(p)
        results[name] = box
        vis = frame.copy()
        if box:
            x, y, bw, bh = box
            cv2.rectangle(vis, (x, y), (x + bw, y + bh), (0, 255, 0), 4)
            label = f"{name} x{x},y{y},{bw}x{bh}"
        else:
            label = f"{name} NONE"
        # 縮圖 + 標題，供總覽
        th = cv2.resize(vis, (384, 216))
        cv2.putText(th, label, (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        thumbs.append(th)

    # 拼 montage: 每列 4 張
    cols = 4
    rows = (len(thumbs) + cols - 1) // cols
    canvas = np.zeros((rows * 216, cols * 384, 3), dtype=np.uint8)
    for i, th in enumerate(thumbs):
        r, c = divmod(i, cols)
        canvas[r*216:(r+1)*216, c*384:(c+1)*384] = th
    montage_path = os.path.join(out_dir, "montage.jpg")
    cv2.imwrite(montage_path, canvas)

    # 存提議座標
    import json
    with open(os.path.join(out_dir, "proposals.json"), "w") as f:
        json.dump(results, f, indent=2)
    found = sum(1 for v in results.values() if v)
    print(f"proposed {found}/{len(results)} frames; montage -> {montage_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
