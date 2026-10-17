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

Pick 3 / Pick 4 (confirmed from each game's own "How to Play" page,
https://www.thecaribbeanlottery.com/games/pick-3 and /pick-4 - not guessed
from the result-data shape): 4 sessions daily - Morning 10:00 AM, Midday
1:00 PM, Sunset 6:00 PM, Evening 9:00 PM. Each session genuinely draws
THREE independent digit sets ("1st"/"2nd"/"3rd" spot - confirmed via
check-history, and explained by the game's own real prize table, which
pays a different descending amount per spot for the same bet type, e.g.
Pick 3 Straight: 1st $400, 2nd $75, 3rd $50). One ticket is checked against
all three. "Mega Ball" is a real optional purchase-time add-on, modeled as
`mega_ball_drawn` (bool, not a digit) per session - confirmed real via a
wider 9-date/36-session sample (14/32 real sessions hit, e.g. 2026-10-02
Morning/Sunset), not the rare/non-existent outcome an earlier 5-date
sample wrongly concluded. The live page's HTML shows either a literal
"NONE" row (not drawn) or a colored-marker row with no text (drawn) - see
`scrape_pick_game`'s Mega Ball extraction below.
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


SESSION_SLUG = {"morning": "morning", "midday": "midday", "sunset": "sunset", "evening": "evening"}


def scrape_pick_game(slug, digit_count):
    """Pick 3 (digit_count=3) / Pick 4 (digit_count=4). Uses check-history for yesterday (same
    reason as Caribbean Numbers: the live page shows "Awaiting Results" for today until each
    session actually fires, so yesterday via check-history reliably gets a fully-completed day)."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    html = fetch_history(slug, yesterday)
    entries = []
    for m in re.finditer(
        r'icon-\w+"></span>\s*([A-Za-z]+)\s*<br[^>]*>\s*#(\d+)</label>(.*?)'
        r'(?=<div class="flex center">|</div>\s*</div>\s*</li>)',
        html, re.S,
    ):
        label, _draw_id, block = m.groups()
        session = SESSION_SLUG.get(label.strip().lower())
        if not session:
            continue
        spot_groups = re.findall(
            r'<li>(1st|2nd|3rd)</li>(.*?)(?=<li>(?:1st|2nd|3rd|Mega Ball)</li>|</ul>\s*</div>)',
            block, re.S,
        )
        spots = []
        for _tag, nb in spot_groups:
            nums = [int(x) for x in re.findall(r"<li>(\d+)</li>", nb)]
            if len(nums) == digit_count:
                spots.append(nums)
        if len(spots) != 3:
            continue
        # Mega Ball: a real per-draw published outcome, not a digit. The real HTML shows either a
        # literal "NONE" row (not drawn) or a colored-marker row with no text, e.g.
        # `<li style="...background-color: blue">&nbsp;</li>` (drawn) - confirmed via a real hit,
        # 2026-10-02 Morning/Sunset both showed the colored-marker form. An earlier pass only
        # checked a handful of all-"NONE" dates and wrongly concluded this was never active.
        mega_ball_drawn = False
        mb_match = re.search(r"Mega Ball</li>\s*<li([^>]*)>(.*?)</li>", block, re.S)
        if mb_match:
            mega_ball_drawn = mb_match.group(2).strip() != "NONE"
        entries.append({
            "draw_date": yesterday,
            "session": session,
            "mega_ball_drawn": mega_ball_drawn,
            "digits": spots[0],
            "digits_2nd": spots[1],
            "digits_3rd": spots[2],
        })
    if not entries:
        log.error("%s: no parseable result for %s", slug, yesterday)
        return
    save(slug, entries, ["draw_date", "session"])


def scrape_pick3():
    scrape_pick_game("pick-3", 3)


def scrape_pick4():
    scrape_pick_game("pick-4", 4)


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
    for fn in (scrape_super_lotto, scrape_lucky_pick, scrape_pay_day, scrape_caribbean_numbers,
               scrape_pick3, scrape_pick4):
        try:
            fn()
        except Exception as e:
            log.error("%s failed: %s", fn.__name__, e)
        time.sleep(1)


if __name__ == "__main__":
    main()
