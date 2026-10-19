#!/usr/bin/env python3
"""West Virginia Lottery own-games scraper.

Source: wvlottery.com's own draw-games pages (e.g.
https://wvlottery.com/games/draw-games/daily-3) are a Next.js app whose
server-rendered HTML embeds the full live draw-games catalog (including
every game's latest completed drawing) as a React Server Components (RSC)
payload inside a `self.__next_f.push([1, "..."])` call. That payload's
second element is itself a JSON string (`"<chunk-id>:<json>"`), found via
the user's own browser devtools - re-verified this session with a plain
`requests.get` (HTTP 200, no auth/JS execution needed). Any one draw-games
page returns the same full catalog, so a single fetch covers all of WV's
own games at once.

Per explicit user direction this session, only Daily 3, Daily 4 and Cash 25
are modeled. Keno (continuous, ~every 3 minutes) is excluded under the
standing high-frequency policy; Cash Pop is excluded because - like other
states' Cash Pop games - its real prize is a random per-ticket multiplier,
not computable from the public draw feed (same class as IN Cash POP).

Real confirmed schedule, quoted from wvlottery.com's own in-store how-to-play
text embedded in the same page:
- Daily 3 / Daily 4: "Tickets must be purchased by 6:49 p.m., ET, Monday
  through Saturday" / "Watch the DAILY game drawings on your local
  television station at 6:59 PM" -> 6:59 PM ET, Monday-Saturday (no Sunday).
- Cash 25: "Tickets must be purchased by 6:49 p.m., ET, Monday, Tuesday,
  Thursday, and Friday" -> 6:59 PM ET on those four days only.

Each game's jackpotResults list mixes the upcoming (OPEN) draw with past
(PAYABLE) ones; this scraper takes the most recent PAYABLE entry per game.
"""
import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA}
PAGE_URL = "https://wvlottery.com/games/draw-games/daily-3"
ET = ZoneInfo("America/New_York")

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "west-virginia")
MAX_HISTORY = 100

# gameNumber (WV's own id, stable across pages) -> (folder name, digit count)
GAMES = {
    "9": "daily-3",
    "14": "daily-4",
    "10": "cash-25",
}


def extract_draw_games(html):
    marker = "self.__next_f.push(["
    search_from = 0
    while True:
        idx = html.find(marker, search_from)
        if idx == -1:
            return None
        arr_start = idx + len(marker) - 1  # position of the '[' of [1, "..."]
        dec = json.JSONDecoder()
        try:
            obj, end = dec.raw_decode(html, arr_start)
        except (ValueError, json.JSONDecodeError):
            search_from = idx + 1
            continue
        if (
            isinstance(obj, list)
            and len(obj) == 2
            and isinstance(obj[1], str)
            and "drawGames" in obj[1]
        ):
            chunk = obj[1]
            _, _, json_part = chunk.partition(":")
            data = json.loads(json_part)
            for item in data:
                if isinstance(item, dict) and "drawGames" in item:
                    return item["drawGames"]
            return None
        search_from = end


def save(game_folder, new_entries):
    path = os.path.join(OUT_DIR, game_folder, "latest.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = []
    if os.path.exists(path):
        with open(path) as f:
            existing = json.load(f)

    merged = {e["draw_date"]: e for e in existing}
    for e in new_entries:
        merged[e["draw_date"]] = e

    out = sorted(merged.values(), key=lambda e: e["draw_date"], reverse=True)[:MAX_HISTORY]
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    log.info("saved %s (%d entries)", path, len(out))


def main():
    r = requests.get(PAGE_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    draw_games = extract_draw_games(r.text)
    if not draw_games:
        log.error("could not locate drawGames payload in page")
        return

    by_number = {g.get("gameNumber"): g for g in draw_games}

    for game_number, folder in GAMES.items():
        game = by_number.get(game_number)
        if not game:
            log.warning("game %s (%s) not found in payload", game_number, folder)
            continue
        jr = game.get("jackpotResults") or {}
        results = jr.get("jackpotResults") or []
        payable = [res for res in results if res.get("jackpotStatus") == "PAYABLE"]
        if not payable:
            log.warning("%s: no PAYABLE draw found", folder)
            continue
        latest = payable[0]
        result_data = sorted(latest.get("resultData", []), key=lambda x: x["order"])
        numbers = [int(rd["data"]) for rd in result_data]
        if not numbers:
            log.warning("%s: empty resultData", folder)
            continue
        entry = {
            "draw_date": latest["drawingDate"],
            "numbers": numbers,
            "fetched_at": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
            "source_url": PAGE_URL,
        }
        save(folder, [entry])


if __name__ == "__main__":
    main()
