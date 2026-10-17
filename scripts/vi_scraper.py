#!/usr/bin/env python3
"""US Virgin Islands (The Caribbean Lottery) own-games scraper.

Source: thecaribbeanlottery.com - this is the real operator serving USVI
(confirmed: USVI is listed among its "WHERE TO PLAY?" locations, and its
own per-territory pricing notes explicitly call out USVI). The two domains
from an earlier research pass (usvilottery.com - parked/for-sale; the
other guessed domain - genuinely 403-blocked) were dead ends; this is a
different, real, reachable domain. robots.txt: `Allow: /`. Plain HTML,
no JS/API needed - results are server-rendered directly into the page.

Real confirmed schedule (quoted from
https://www.thecaribbeanlottery.com/game-schedules, a real schedule table,
not guessed):
- Super Lotto: Tue/Fri 9:00 PM. 5 numbers 1-35 + "Super Ball" 1-10.
  Confirmed from the game's own "How to Play" text: "the first set from 1
  to 35 and the second set from 1 to 10 ... in St. Maarten and USVI each
  play costs US$2."
- Lucky Pick: daily 8:00 PM. 5 numbers 1-28, no bonus ball. Each draw also
  produces a "LUCKY PICK DOUBLE PLAY" - a second, separately drawn 5-number
  set at the same session (same pattern as Powerball Double Play / Puerto
  Rico's Revancha) - built as its own game. Per-ticket Multiplier exists
  but isn't modelable from public draw data (same class as other states'
  unmodeled multiplier mechanics).
- Pay Day: daily 7:30 PM, confirmed from the game's own "How to Play" text:
  "Drawings are held daily at 7:30p.m." 4 numbers 1-35 + "Cash Ball" 1-6.
  Per-ticket Multiplier (2X-10X) also not modelable.
- Caribbean Numbers!: 3 sessions daily (1 PM, 3 PM, 9 PM), confirmed from
  the schedule table. Each session draws 3 separate 2-digit (00-99)
  numbers used for a Quiniela/Pale/Tripleta betting system - built
  DISPLAY-ONLY (showing the 3 numbers per session) since the real
  single/double/triple match-modality logic is a genuinely different,
  complex mechanic not yet modeled anywhere else in this project; full
  pick/match logic deliberately not attempted this round.

Pick 3 and Pick 4 are NOT built here - their real historical data (via the
site's own check-history endpoint) turned out to show three separate
numbered result sets ("1st"/"2nd"/"3rd") plus an optional "Mega Ball" per
session, not the simple single 3-/4-digit draw the landing page implied -
flagged as an open question rather than guessed at.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA}
BASE = "https://www.thecaribbeanlottery.com"

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "us-virgin-islands")
MAX_HISTORY = 100

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
DATE_RE = re.compile(r"([A-Za-z]+),\s*([A-Za-z]+)\s+(\d{1,2})\w*,\s*(\d{4})")


def parse_time_tag(html):
    m = DATE_RE.search(html)
    if not m:
        return None
    _, month_name, day, year = m.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    return f"{year}-{month:02d}-{int(day):02d}"


def fetch_live(slug):
    r = requests.get(f"{BASE}/games/{slug}", headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def fetch_history(slug, date_str):
    r = requests.post(f"{BASE}/check-history/{slug}", data={"date": date_str}, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def extract_ball_groups(html):
    """Returns {label: [numbers]} for every `ball-results` block in the
    LAST DRAW section (works for both the live page and check-history)."""
    idx = html.find("LAST DRAW")
    if idx == -1:
        idx = html.find("RESULTS FOR")
    chunk = html[idx: idx + 3000] if idx != -1 else html
    groups = {}
    for m in re.finditer(r"<label>([^<]+)</label>\s*<ul[^>]*>(.*?)</ul>", chunk, re.S):
        label, block = m.groups()
        nums = [int(x) for x in re.findall(r"<li[^>]*>(\d+)</li>", block)]
        if nums:
            groups[label.strip()] = nums
    return groups, parse_time_tag(chunk)


def save(game_folder, new_entries, key_fields):
    path = os.path.join(OUT_DIR, game_folder, "latest.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = []
    if os.path.exists(path):
        with open(path) as f:
            existing = json.load(f)

    def key(e):
        return tuple(e.get(k) for k in key_fields)

    merged = {key(e): e for e in existing}
    for e in new_entries:
        e["fetched_at"] = datetime.now(timezone.utc).isoformat()
        e["source_url"] = f"{BASE}/games/{game_folder}"
        merged[key(e)] = e

    out = sorted(merged.values(), key=lambda e: (e["draw_date"], e.get("session") or ""), reverse=True)[:MAX_HISTORY]
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    log.info("saved %s (%d entries)", path, len(out))


def scrape_super_lotto():
    html = fetch_live("super-lotto")
    groups, date = extract_ball_groups(html)
    if not date or "White Balls" not in groups:
        log.error("super-lotto: no parseable result")
        return
    entry = {"draw_date": date, "numbers": groups["White Balls"]}
    if "Super Ball" in groups:
        entry["super_ball"] = groups["Super Ball"][0]
    save("super-lotto", [entry], ["draw_date"])


def scrape_lucky_pick():
    html = fetch_live("lucky-pick")
    groups, date = extract_ball_groups(html)
    if not date or "Winning Numbers" not in groups:
        log.error("lucky-pick: no parseable result")
        return
    save("lucky-pick", [{"draw_date": date, "numbers": groups["Winning Numbers"]}], ["draw_date"])
    if "LUCKY PICK DOUBLE PLAY" in groups:
        save("lucky-pick-double-play", [{"draw_date": date, "numbers": groups["LUCKY PICK DOUBLE PLAY"]}], ["draw_date"])


def scrape_pay_day():
    html = fetch_live("pay-day")
    groups, date = extract_ball_groups(html)
    if not date or "White Balls" not in groups:
        log.error("pay-day: no parseable result")
        return
    entry = {"draw_date": date, "numbers": groups["White Balls"]}
    if "Cash Ball" in groups:
        entry["cash_ball"] = groups["Cash Ball"][0]
    save("pay-day", [entry], ["draw_date"])


def scrape_caribbean_numbers():
    # Live page uses the same in-progress "Awaiting Results" pattern as
    # Pick 3/4 for today's date - use yesterday's date via check-history to
    # reliably get a fully-completed day every run.
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    html = fetch_history("caribbean-numbers", yesterday)
    entries = []
    for m in re.finditer(
        r'icon-\w+"></span>\s*(\d+\s*[AP]M)\s*<br[^>]*>\s*#(\d+)</label>\s*<ul[^>]*>\s*<li>Draw Results</li>(.*?)</ul>',
        html, re.S,
    ):
        session_label, _draw_id, nums_block = m.groups()
        nums = [int(x) for x in re.findall(r"<li>(\d+)</li>", nums_block)]
        if len(nums) != 3:
            continue
        entries.append({
            "draw_date": yesterday,
            "session": session_label.strip().replace(" ", "").lower(),
            "numbers": nums,
        })
    if not entries:
        log.error("caribbean-numbers: no parseable result for %s", yesterday)
        return
    save("caribbean-numbers", entries, ["draw_date", "session"])


def main():
    for fn in (scrape_super_lotto, scrape_lucky_pick, scrape_pay_day, scrape_caribbean_numbers):
        try:
            fn()
        except Exception as e:
            log.error("%s failed: %s", fn.__name__, e)
        time.sleep(1)


if __name__ == "__main__":
    main()
