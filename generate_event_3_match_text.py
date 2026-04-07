from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


BASE_URL = "https://wego-tkd-web.onrender.com"
EVENT_ID = 3
OUTPUT_FILE = Path(__file__).with_name("對打第二天-各場地對戰資訊.txt")


def fetch_json(path: str) -> Any:
    request = Request(
        f"{BASE_URL}{path}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request) as response:
        return json.load(response)


def simplify_team_name(team: str) -> str:
    cleaned = team.strip()
    patterns = [
        r"^(?:臺|台)北市立",
        r"^(?:新北|臺北|台北|新竹|基隆|台中|臺中|桃園|臺南|台南|高雄)市",
        r"^(?:臺|台)北縣",
        r"^基隆市",
        r"^新竹縣",
        r"^屏東縣",
        r"^宜蘭縣",
        r"^花蓮縣",
        r"^台東縣",
        r"^臺東縣",
        r"^苗栗縣",
        r"^彰化縣",
        r"^南投縣",
        r"^雲林縣",
        r"^嘉義縣",
        r"^嘉義市",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned)
    return cleaned.strip() or team.strip()


def simplify_category_name(category_name: str) -> str:
    text = category_name.strip()
    level = ""
    if "國中" in text:
        level = "(國)"
    elif "高中社會" in text:
        level = "(社高)"

    gender = "女子" if "女子" in text else "男子" if "男子" in text else text
    match = re.search(r"(\d+KG\+?|\d+KG)", text)
    weight = match.group(1) if match else ""
    if gender in {"女子", "男子"} and weight:
        return f"{level}{gender}{weight}"
    return text


def format_competitor(team: str, name: str, is_winner: bool) -> str:
    competitor = " ".join(
        part for part in [simplify_team_name(team), name.strip()] if part
    )
    if is_winner:
        competitor += " (勝)"
    return competitor or "資料缺漏"


def court_heading(court_id: int) -> str:
    chinese_numbers = {
        1: "一",
        2: "二",
        3: "三",
        4: "四",
        5: "五",
        6: "六",
        7: "七",
        8: "八",
        9: "九",
        10: "十",
        11: "十一",
        12: "十二",
    }
    number = chinese_numbers.get(court_id, str(court_id))
    return f"第{number}場地"


def match_sort_key(match_number: Any) -> tuple[int, ...]:
    parts = str(match_number).split("-")
    key: list[int] = []
    for part in parts:
        try:
            key.append(int(part))
        except ValueError:
            key.append(999999)
    return tuple(key)


def load_matches() -> dict[int, list[dict[str, Any]]]:
    categories = fetch_json(f"/api/public/events/{EVENT_ID}/categories")
    grouped_matches: dict[int, list[dict[str, Any]]] = defaultdict(list)
    seen_match_ids: set[str] = set()

    for category in categories:
        category_id = category["id"]
        category_name = category["name"]
        schedule = fetch_json(
            f"/api/public/events/{EVENT_ID}/categories/{category_id}/schedule"
        )

        for match in schedule.get("data", []):
            match_id = str(match.get("match_id") or "").strip()
            match_number = match.get("matchnumber")
            court_id = match.get("courtid")
            player1_id = match.get("player1_id")
            player2_id = match.get("player2_id")
            winner_id = match.get("winner_id")

            if not match_id or match_id in seen_match_ids:
                continue
            if court_id in (None, "") or match_number in (None, ""):
                continue
            if player1_id == "BYE" or player2_id == "BYE":
                continue

            seen_match_ids.add(match_id)
            grouped_matches[int(court_id)].append(
                {
                    "match_number": str(match_number),
                    "match_sort_key": match_sort_key(match_number),
                    "left": format_competitor(
                        match.get("p1_team", ""),
                        match.get("p1_display", ""),
                        winner_id == player1_id,
                    ),
                    "right": format_competitor(
                        match.get("p2_team", ""),
                        match.get("p2_display", ""),
                        winner_id == player2_id,
                    ),
                    "category_name": simplify_category_name(category_name),
                    "round": match.get("round", ""),
                }
            )

    for matches in grouped_matches.values():
        matches.sort(key=lambda item: item["match_sort_key"])

    return dict(sorted(grouped_matches.items()))


def build_output(grouped_matches: dict[int, list[dict[str, Any]]]) -> str:
    lines: list[str] = []

    for index, (court_id, matches) in enumerate(grouped_matches.items()):
        if index > 0:
            lines.append("")
        lines.append(court_heading(court_id))
        for match in matches:
            lines.append(
                f"{match['match_number']} {match['left']} vs {match['right']}"
                f" | {match['category_name']} | {match['round']}"
            )

    return "\n".join(lines) + "\n"


def main() -> None:
    grouped_matches = load_matches()
    OUTPUT_FILE.write_text(build_output(grouped_matches), encoding="utf-8")
    total_matches = sum(len(matches) for matches in grouped_matches.values())
    print(f"Wrote {OUTPUT_FILE.name} with {total_matches} matches across {len(grouped_matches)} courts.")


if __name__ == "__main__":
    main()