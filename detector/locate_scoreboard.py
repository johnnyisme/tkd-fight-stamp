#!/usr/bin/env python3
"""
記分板定位通則（v2）——靠記分板的「指紋」而非畫面座標。

場地到處是紅藍（地墊、護具、布條），單靠找紅塊藍塊會滿畫面誤配。
記分板真正獨一無二的結構是一橫排：

    [ 紅塊(大數字) ][ 深色窄欄(計時器/MATCH/ROUND) ][ 藍塊(大數字) ]

關鍵指紋 = 中間那條「夾在紅與藍之間的深色欄」。地墊的紅藍直接相接、
沒有中間深色欄；布條/護具也沒有這個三明治結構。

演算法：
  1. 各自找出夠大、夠方正的紅塊與藍塊。
  2. 對每一對「紅在左、藍在右、等大、上下對齊、水平靠近」的組合，
     檢查中間夾縫是否為深色（低亮度）——這是決定性條件。
  3. 通過的合併成記分板 bbox；多個候選取分數最高。

不寫死任何畫面座標；只依賴記分板自身的相對結構，故可跨角度/大小/位置。
"""
import sys
import cv2
import numpy as np


def find_color_blocks(hsv, ranges, min_area_frac, frame_area):
    mask = None
    for lower, upper in ranges:
        m = cv2.inRange(hsv, np.array(lower), np.array(upper))
        mask = m if mask is None else cv2.bitwise_or(mask, m)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blocks = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < min_area_frac * frame_area:
            continue
        ar = w / max(h, 1)
        # 記分板單邊比分塊大致方形（略寬），排除細長條（地墊邊線、布條）
        if ar < 0.5 or ar > 2.2:
            continue
        # 填充率：真的色塊填充高，鏤空的地墊輪廓填充低
        fill = cv2.contourArea(c) / max(area, 1)
        if fill < 0.6:
            continue
        blocks.append((x, y, w, h))
    return blocks


def gap_is_dark(gray, red, blue):
    """檢查紅塊右緣與藍塊左緣之間的夾縫是否為深色欄（記分板指紋）。"""
    rx, ry, rw, rh = red
    bx, by, bw, bh = blue
    gap_left = rx + rw
    gap_right = bx
    if gap_right - gap_left < 3:
        # 幾乎貼死（像地墊直接相接）——量兩塊交界一小條
        gap_left = rx + rw - 2
        gap_right = bx + 2
    # 夾縫垂直範圍取兩塊重疊處
    top = max(ry, by)
    bot = min(ry + rh, by + bh)
    if bot - top < 5 or gap_right <= gap_left:
        return False, 255.0
    strip = gray[top:bot, gap_left:gap_right]
    if strip.size == 0:
        return False, 255.0
    mean_val = float(strip.mean())
    # 深色欄：平均亮度低。地墊紅藍相接處是高亮度。
    return mean_val < 90, mean_val


def locate(frame):
    h, w = frame.shape[:2]
    frame_area = h * w
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    red_ranges = [((0, 110, 80), (10, 255, 255)), ((168, 110, 80), (180, 255, 255))]
    blue_ranges = [((100, 110, 80), (128, 255, 255))]
    reds = find_color_blocks(hsv, red_ranges, 0.0010, frame_area)
    blues = find_color_blocks(hsv, blue_ranges, 0.0010, frame_area)

    best = None
    best_score = 0.0
    best_dbg = None
    for red in reds:
        rx, ry, rw, rh = red
        for blue in blues:
            bx, by, bw, bh = blue
            if bx < rx:  # 紅在左、藍在右
                continue
            ra, ba = rw * rh, bw * bh
            size_ratio = min(ra, ba) / max(ra, ba)
            if size_ratio < 0.45:  # 兩塊等大
                continue
            rcy, bcy = ry + rh / 2, by + bh / 2
            if abs(rcy - bcy) > 0.5 * max(rh, bh):  # 垂直對齊
                continue
            gap = bx - (rx + rw)
            if gap > 0.9 * rw or gap < -0.15 * rw:  # 水平靠近
                continue
            dark, mean_val = gap_is_dark(gray, red, blue)
            if not dark:  # 決定性條件：中間夾縫必須是深色欄
                continue
            score = size_ratio * (ra + ba) * (120 - mean_val)
            if score > best_score:
                best_score = score
                x0, y0 = min(rx, bx), min(ry, by)
                x1, y1 = max(rx + rw, bx + bw), max(ry + rh, by + bh)
                best = (x0, y0, x1 - x0, y1 - y0)
                best_dbg = (red, blue, mean_val)
    return best, reds, blues, best_dbg


def main(paths):
    for p in paths:
        frame = cv2.imread(p)
        if frame is None:
            print(f"{p}: cannot read")
            continue
        bbox, reds, blues, dbg = locate(frame)
        annotated = frame.copy()
        for (x, y, bw, bh) in reds:
            cv2.rectangle(annotated, (x, y), (x + bw, y + bh), (0, 0, 255), 1)
        for (x, y, bw, bh) in blues:
            cv2.rectangle(annotated, (x, y), (x + bw, y + bh), (255, 0, 0), 1)
        name = p.rsplit("/", 1)[-1]
        if bbox:
            x, y, bw, bh = bbox
            cv2.rectangle(annotated, (x, y), (x + bw, y + bh), (0, 255, 0), 3)
            print(f"{name}: FOUND bbox=x{x} y{y} w{bw} h{bh}  "
                  f"gap_dark_mean={dbg[2]:.0f}  (reds={len(reds)} blues={len(blues)})")
        else:
            print(f"{name}: NONE  (reds={len(reds)} blues={len(blues)})")
        cv2.imwrite(p.rsplit(".", 1)[0] + ".annot.jpg", annotated)


if __name__ == "__main__":
    main(sys.argv[1:])
