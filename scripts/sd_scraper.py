#!/usr/bin/env python3
"""South Dakota Lottery own-games scraper.

Source: lottery.sd.gov's own public IGT/AWC results API - found via the
user's own browser devtools, independently re-verified this session with a
plain curl (HTTP 200, no auth header needed, unlike the same IGT/AWC
platform family used by other states such as Rhode Island/Puerto Rico
which require an auth token).

Dakota Cash is SD's only own game: 5 numbers from 1-35, $1/play. Optional
EZ Match instant-win add-on exists but isn't modelable from public draw
data (same class as other states' unmodeled secondary add-ons).

Real confirmed schedule: lottery.sd.gov's own game page
(https://lottery.sd.gov/game/dakota-cash/) states "Drawings are every
Wednesday and Saturday night" and "Tickets may be purchased until 9 p.m.
central on the evening of the draw." The real API's own `drawTime` field is
consistently 9:15 PM CT across every real draw checked this session - used
as the exact minute (ticket sales close at 9, draw itself 15 min later).
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
API_URL = "https://lottery.sd.gov/api/igt/games/v2/draw-games/draws/?game-names=Dakota%20Cash&previous-draws=10"
CT = ZoneInfo("America/Chicago")

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "south-dakota")
MAX_HISTORY = 100


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
    r = requests.get(API_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    entries = []
    for draw in data.get("draws", []):
        results = draw.get("results")
        if not results:
            continue
        primary = results[0].get("primary")
        if not primary:
            continue
        draw_time_ms = draw.get("drawTime")
        draw_dt_ct = datetime.fromtimestamp(draw_time_ms / 1000, tz=CT)
        entries.append({
            "draw_date": draw_dt_ct.strftime("%Y-%m-%d"),
            "numbers": [int(n) for n in primary],
            "fetched_at": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
            "source_url": "https://lottery.sd.gov/game/dakota-cash/",
        })
    if not entries:
        log.error("no draws with results parsed")
        return
    save("dakota-cash", entries)


if __name__ == "__main__":
    main()
