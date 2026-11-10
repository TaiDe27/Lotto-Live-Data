#!/usr/bin/env python3
"""Texas Lottery own-games scraper.

Source: texaslottery.com's own server-rendered game pages (old-school .html
structure, no JS/API needed). Each game's page embeds its own latest winning
numbers directly in static HTML under the "#LastDraw" tab — verified by direct
fetch, not guessed. robots.txt was not checked to disallow these paths.

Real confirmed draw schedule (quoted from each game's own "How to Play" text,
All times Central):
- Lotto Texas: Mon/Wed/Sat 10:12 PM
- Texas Two Step: Mon/Thu 10:12 PM
- Pick 3 / Daily 4: Mon-Sat, 4 sessions/day (Morning 10:00 AM, Day 12:27 PM,
  Evening 6:00 PM, Night 10:12 PM), with a FIREBALL add-on digit
- Cash Five: Mon-Sat 10:12 PM
- All or Nothing: Mon-Sat, 4 sessions/day (same 4 times as Pick 3/Daily 4),
  pick 12 of 24, no add-on
"""
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA}
BASE = "https://www.texaslottery.com/export/sites/lottery/Games"

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "texas")
MAX_HISTORY = 100


def fetch(path):
    url = f"{BASE}/{path}/index.html"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def parse_date(label):
    """'10/16/2026 Morning' -> ('2026-10-16', 'morning')."""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})\s*(\w*)", label.strip())
    if not m:
        return None, None
    mm, dd, yyyy, session = m.groups()
    return f"{yyyy}-{mm}-{dd}", session.lower() or None


def extract_sessions(html, ball_class="winningNumberBalls", has_fireball=False, has_bonus_ball=False):
    """Return list of {date, session, numbers, fireball?, bonus_ball?} for each
    session block found under #LastDraw. Handles two real page layouts:
    multi-session games (Pick 3/Daily 4/All or Nothing) use a <a class=
    "detailsLink"> date+session label; single-session games (Lotto Texas,
    Texas Two Step, Cash Five) put the date directly in an <h2> instead."""
    idx = html.find('id="LastDraw"')
    if idx == -1:
        return []
    next_idx = html.find('id="PastResults"', idx)
    chunk = html[idx: next_idx if next_idx != -1 else idx + 20000]

    results = []

    def numbers_from_block(block):
        return [int(x) for x in re.findall(r"<span[^>]*>(\d+)</span>", block)]

    # Multi-session layout
    pattern = re.compile(
        r'class="detailsLink"[^>]*>([^<]+)</a>\s*'
        r'<ol class="' + re.escape(ball_class) + r'[^"]*">(.*?)</ol>'
        r'(?:\s*<ol class="' + re.escape(ball_class) + r'[^"]*">(.*?)</ol>)?',
        re.S,
    )
    for m in pattern.finditer(chunk):
        label, main_block, second_block = m.groups()
        date, session = parse_date(label)
        if not date:
            continue
        entry = {"draw_date": date, "session": session, "numbers": numbers_from_block(main_block)}
        if has_fireball and second_block:
            fb = re.search(r'fireball">\s*(\d+)\s*<', second_block)
            if fb:
                entry["fireball"] = int(fb.group(1))
        results.append(entry)
    if results:
        return results

    # Single-session layout: date lives inside the <h2>
    m = re.search(
        r"Winning Numbers for\s*(\d{2}/\d{2}/\d{4})\s*are:</h2>\s*"
        r'<ol class="' + re.escape(ball_class) + r'[^"]*">(.*?)</ol>',
        chunk,
        re.S,
    )
    if m:
        date_raw, block = m.groups()
        mm, dd, yyyy = date_raw.split("/")
        date = f"{yyyy}-{mm}-{dd}"
        all_nums = numbers_from_block(block)
        entry = {"draw_date": date, "session": None, "numbers": all_nums}
        if has_bonus_ball:
            bm = re.search(r'class="bonusball">(\d+)<', block)
            if bm:
                bonus = int(bm.group(1))
                entry["numbers"] = all_nums[:-1] if all_nums and all_nums[-1] == bonus else all_nums
                entry["bonus_ball"] = bonus
        results.append(entry)
    return results


def next_draw_date(after_date_iso):
    """texaslottery.com's own Pick 3/Daily 4 page shows only past results under #LastDraw, no
    forward-looking element (confirmed live 2026-11-10) — the page's own text says drawings run
    "every Monday - Saturday" for all 4 sessions, so this computes the next Mon-Sat day after the
    most recent real result rather than scraping a date that isn't published."""
    d = date.fromisoformat(after_date_iso)
    for offset in range(1, 8):
        candidate = d + timedelta(days=offset)
        if candidate.weekday() != 6:  # skip Sunday
            return candidate.isoformat()
    raise AssertionError("unreachable: a Mon-Sat day occurs at least once within a week")


def add_next_draw_dates(entries):
    """Groups `entries` (already session-tagged by `extract_sessions`) by session and stamps each
    with the next occurrence of ITS OWN session after that session's most recent real draw_date —
    multi-session games here draw 4x/day, so a single global next-date would misattribute which
    session is actually next."""
    by_session = {}
    for e in entries:
        by_session.setdefault(e.get("session"), []).append(e)
    for session, group in by_session.items():
        latest = max(e["draw_date"] for e in group)
        nd = next_draw_date(latest)
        for e in group:
            e["next_draw_date"] = nd
    return entries


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
    updated = 0
    for e in new_entries:
        e["fetched_at"] = datetime.now(timezone.utc).isoformat()
        e["source_url"] = "https://www.texaslottery.com/"
        k = key(e)
        if k not in merged or merged[k].get("fetched_at") != e.get("fetched_at"):
            updated += 1
        merged[k] = e

    out = sorted(merged.values(), key=lambda e: (e["draw_date"], e.get("session") or ""), reverse=True)[:MAX_HISTORY]
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    log.info("saved %s (%d entries, %d touched this run)", path, len(out), updated)


GAMES = [
    ("Lotto_Texas", "lotto-texas", "numbers", False, False, ["draw_date"]),
    ("Texas_Two_Step", "two-step", "numbers", False, True, ["draw_date"]),
    ("Cash_Five", "cash-five", "numbers", False, False, ["draw_date"]),
    ("Pick_3", "pick3", "numbers", True, False, ["draw_date", "session"]),
    ("Daily_4", "daily4", "numbers", True, False, ["draw_date", "session"]),
    ("All_or_Nothing", "all-or-nothing", "numbersAon", False, False, ["draw_date", "session"]),
]


def main():
    for path, folder, kind, has_fireball, has_bonus_ball, key_fields in GAMES:
        try:
            html = fetch(path)
        except Exception as e:
            log.error("fetch failed for %s: %s", path, e)
            continue
        ball_class = "winningNumberBallsAon" if kind == "numbersAon" else "winningNumberBalls"
        sessions = extract_sessions(html, ball_class=ball_class, has_fireball=has_fireball, has_bonus_ball=has_bonus_ball)
        if not sessions:
            log.error("no sessions parsed for %s", path)
            continue
        if folder in ("pick3", "daily4"):
            sessions = add_next_draw_dates(sessions)
        save(folder, sessions, key_fields)
        time.sleep(1)


if __name__ == "__main__":
    main()
