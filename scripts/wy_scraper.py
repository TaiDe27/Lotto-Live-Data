#!/usr/bin/env python3
"""Wyoming Lottery own-game scraper.

Cowboy Draw is Wyoming's only own game (all other WY games are shared
multi-state ones already covered elsewhere). Source: wyolotto.com's own
backend, a Gambyt-hosted draw-data API found via the user's own browser
devtools:

  https://prod.game-data.gambytservices.com/v1/draw-games/<drawGameId>/
  current-and-previous-drawings?asOf=<ISO-8601 UTC timestamp>

This endpoint DOES require an API key - it's not a server-side secret, just
a public key the wyolotto.com frontend sends on every request (visible to
anyone who opens that site's own devtools, same `x-client-id:
product-portal-server` value for all visitors), re-captured from the user's
own browser session and confirmed working with a plain `requests.get` using
the same `x-api-key`/`x-client-id`/`Origin`/`Referer` headers. If this ever
starts 401'ing again, re-capture a fresh key the same way (devtools Network
tab on https://wyolotto.com/games/cowboy-draw, any request to this host).

It returns `previousDrawing` (the most recently completed draw) and
`currentDrawing` (the upcoming one, `results.status == "PENDING"` until
it happens). This scraper always takes `previousDrawing`.

Real confirmed rules, from lotteryusa.com's own Cowboy Draw page (the
official wyolotto.com game page itself is a client-rendered SPA with no
rules in its static HTML): "Pick five numbers from a set of 1 to 45" /
"$5 per play, with each ticket including two lines minimum" / "Cowboy Draw
numbers are drawn every Monday and Thursday at 2:00 p.m." (Wyoming local
time, i.e. America/Denver - matches this API's own `drawTime` of 20:00 UTC
exactly, since Wyoming was in MDT, UTC-6, as of the draws checked this
session).
"""
import json
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
# Public frontend key wyolotto.com itself sends on every request (not a server secret -
# see module docstring). Re-capture from browser devtools if this ever 401s.
GAMBYT_API_KEY = (
    "ODRmMzIwYTItMDhlYS00MmVlLTk2MjgtYjRmOTQ5MDhlMjRhOitWQVE4WmgvOWRNeGw2eG42UWFPT0lHNWRCbHVaR1NkaTdkTU9yVUYxTnc9"
)
HEADERS = {
    "User-Agent": UA,
    "x-api-key": GAMBYT_API_KEY,
    "x-client-id": "product-portal-server",
    "Origin": "https://wyolotto.com",
    "Referer": "https://wyolotto.com/",
}
DRAW_GAME_ID = "bafaf5b5-8a50-4401-9684-0dbe85b2afb5"
API_URL = (
    f"https://prod.game-data.gambytservices.com/v1/draw-games/{DRAW_GAME_ID}"
    "/current-and-previous-drawings"
)
MT = ZoneInfo("America/Denver")

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "wyoming")
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
    as_of = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    r = requests.get(API_URL, headers=HEADERS, params={"asOf": as_of}, timeout=20)
    r.raise_for_status()
    data = r.json()

    prev = data.get("previousDrawing") or {}
    results = prev.get("results") or {}
    if results.get("status") != "ACTIVE":
        log.warning("previousDrawing not ACTIVE (status=%s), nothing to save", results.get("status"))
        return
    draw_result = results.get("drawResult") or {}
    primary = draw_result.get("primary") or {}
    numbers = primary.get("winningNumbers")
    if not numbers:
        log.error("no winningNumbers in drawResult")
        return

    draw_time = prev.get("when", {}).get("drawTime")
    draw_dt_utc = datetime.fromisoformat(draw_time.replace("Z", "+00:00"))
    draw_date = draw_dt_utc.astimezone(MT).strftime("%Y-%m-%d")

    entry = {
        "draw_date": draw_date,
        "numbers": [int(n) for n in numbers],
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "source_url": "https://www.wyolotto.com/games/cowboy-draw",
    }
    save("cowboy-draw", [entry])


if __name__ == "__main__":
    main()
