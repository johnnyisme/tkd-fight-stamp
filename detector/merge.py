#!/usr/bin/env python3
"""
合併:「時間戳 場次」清單 + 對戰表 API → 完整 YouTube 章節清單。

輸入清單每行:  時間戳 <tab或空格> 場次號
用場次號到 event API 查對戰資訊,產出:
  00:02:32 301 龍門國中 高正安 vs 龍華道舘 吳承熙 (勝) | (國)男子55KG | 32強

用法:merge.py <event_id> <清單.txt> [out.txt]
  event_id 例如 201;清單可用 '-' 從 stdin 讀。
"""
import sys, json, re, urllib.request

BASE = "https://wego-tkd.zeabur.app"


def api(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=25))


# 單位簡化(沿用舊 app.js 邏輯:去掉縣市/市立等前綴)
TEAM_PATTERNS = [
    r"^(?:臺|台)北市立", r"^(?:新北|臺北|台北|新竹|基隆|台中|臺中|桃園|臺南|台南|高雄|嘉義)市立",
    r"^(?:臺|台)北縣立", r"^新竹縣立", r"^屏東縣立", r"^宜蘭縣立", r"^花蓮縣立",
    r"^(?:台|臺)東縣立", r"^苗栗縣立", r"^彰化縣立", r"^南投縣立", r"^雲林縣立", r"^嘉義縣立",
    r"^(?:新北|臺北|台北|新竹|基隆|台中|臺中|桃園|臺南|台南|高雄)市", r"^(?:臺|台)北縣",
    r"^基隆市", r"^新竹縣", r"^屏東縣", r"^宜蘭縣", r"^花蓮縣", r"^台東縣", r"^臺東縣",
    r"^苗栗縣", r"^彰化縣", r"^南投縣", r"^雲林縣", r"^嘉義縣", r"^嘉義市",
]


def simplify_team(team):
    t = (team or "").strip()
    for p in TEAM_PATTERNS:
        t = re.sub(p, "", t)
    return t.strip() or (team or "").strip()


def simplify_category(name):
    """青少年女子42KG級 → (國)女子42KG;青年男子68KG級 → (社高)男子68KG。
    規則:青少年=國中組(國);青年=高中社會組(社高)。"""
    t = (name or "").strip()
    level = ""
    if "青少年" in t or "國中" in t:
        level = "(國)"
    elif "青年" in t or "高中" in t or "社會" in t:
        level = "(社高)"
    gender = "女子" if "女" in t else ("男子" if "男" in t else "")
    # 「68KG以上級」→ 68KG+
    m = re.search(r"(\d+)KG(以上)?", t)
    weight = ""
    if m:
        weight = m.group(1) + "KG" + ("+" if m.group(2) else "")
    return f"{level}{gender}{weight}" if (gender and weight) else t


def build_match_index(event_id):
    """回傳 {場次號: match dict + category_name}。"""
    cats = api(f"/api/public/events/{event_id}/categories")
    idx = {}
    for cat in cats:
        try:
            sch = api(f"/api/public/events/{event_id}/categories/{cat['id']}/schedule")
        except Exception:
            continue
        for m in sch.get("data", []):
            num = str(m.get("matchnumber", "")).strip()
            if not num:
                continue
            m["_category"] = cat["name"]
            idx[num] = m
    return idx


def format_line(ts, num, m):
    if m is None:
        return f"{ts} {num} (對戰表查無此場次)"
    p1 = f"{simplify_team(m.get('p1_team',''))} {m.get('p1_display','')}".strip()
    p2 = f"{simplify_team(m.get('p2_team',''))} {m.get('p2_display','')}".strip()
    w = m.get("winner_id")
    if w and w == m.get("player1_id"):
        p1 += " (勝)"
    elif w and w == m.get("player2_id"):
        p2 += " (勝)"
    cat = simplify_category(m.get("_category", ""))
    rnd = m.get("round", "")
    return f"{ts} {num} {p1} vs {p2} | {cat} | {rnd}"


def main():
    event_id = sys.argv[1]
    src = sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else None
    text = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
    idx = build_match_index(event_id)
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("時間戳"):
            continue
        parts = re.split(r"[\s\t]+", line, maxsplit=1)
        ts = parts[0]
        num = parts[1].strip() if len(parts) > 1 else ""
        m = idx.get(num) if num else None
        lines.append(format_line(ts, num, m))
    result = "\n".join(lines)
    if out:
        open(out, "w", encoding="utf-8").write(result + "\n")
        print(f"寫入 {out}（{len(lines)} 行）")
    print(result)


if __name__ == "__main__":
    main()
