#!/usr/bin/env python3
"""Tennessee Lottery own-games scraper.

Source: lottery.net's TN pages (server-rendered HTML, no JS/API needed).
robots.txt only disallows /en/play/ and /change/ - the game pages used here
are unaffected. Uses each game's /numbers archive page (not the single-
result landing page) since it returns up to 30 days of history in one
fetch, with every session's result embedded directly.

Real confirmed schedule (quoted from each game's own page text):
- Tennessee Cash: 5 numbers 1-35 + a separate "Cash Ball" 1-5, drawn "three
  times a week on Monday, Wednesday, and Friday at 10:30 PM CT/11:30PM ET".
- Cash 3: "Morning: Monday to Saturday: 9:26 AM CT", "Midday: Monday to
  Saturday: 12:28 PM CT", "Evening: Every day 6:28 PM CT" - no Sunday
  Morning/Midday session, confirmed from lottery.net's own cash-3/numbers
  page text ("...aside from Sunday when there is only an Evening draw").
  Has a real Wild Ball add-on digit (drawn separately after the regular
  numbers, confirmed from the page's own "How to Play" text and present in
  the real results markup as a `wild-ball` class).
- Cash 4: same session structure as Cash 3 but "Morning: 9:26 AM CDT",
  "Midday: 12:59 PM CT", "Evening: Every day: 6:59 PM CT" - also has a real
  Wild Ball add-on digit.
- Tennessee Daily Jackpot: 5 numbers, no bonus ball. Pool size confirmed as
  1-38 via the real stated odds "Match 5: 1 in 501,942" (C(38,5)=501,942
  exactly, not a guess). "Draws take place nightly at 9:00 PM CT / 10:00 PM
  ET."
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA}
BASE = "https://www.lottery.net/tennessee"

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "tennessee")
MAX_HISTORY = 100

DATE_RE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})")
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def fetch(slug):
    r = requests.get(f"{BASE}/{slug}/numbers", headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def parse_date(label):
    m = DATE_RE.search(label)
    if not m:
        return None
    month_name, day, year = m.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    return f"{year}-{month:02d}-{int(day):02d}"


def extract_single_session(html, ball_class, has_bonus):
    """Parses lottery.net's single-session-per-day archive pages (Cash,
    Daily Jackpot): repeated `resultDate` blocks each with one `balls` list."""
    entries = []
    for m in re.finditer(
        r'resultDate">(.*?)</div>.*?<ul class="balls[^"]*">(.*?)</ul>',
        html, re.S,
    ):
        date_label, block = m.groups()
        date = parse_date(re.sub(r"<[^>]+>", " ", date_label))
        if not date:
            continue
        nums = [int(x) for x in re.findall(rf'class="{re.escape(ball_class)} ball">(\d+)<', block)]
        bonus = None
        if has_bonus:
            bm = re.search(rf'class="{re.escape(ball_class)} bonus-ball">(\d+)<', block)
            if bm:
                bonus = int(bm.group(1))
        if not nums:
            continue
        entry = {"draw_date": date, "numbers": nums}
        if has_bonus and bonus is not None:
            entry["bonus_ball"] = bonus
        entries.append(entry)
    return entries


def extract_multi_session(html, ball_class):
    """Parses lottery.net's multi-session-per-day archive pages (Cash 3,
    Cash 4): repeated date blocks, each containing several `drawTime`
    sub-blocks (Morning/Midday/Evening), each with its own `balls` list and
    a `wild-ball` digit."""
    entries = []
    date_blocks = re.split(r'(?=<div class="resultDate")', html)
    for block in date_blocks:
        date_m = re.search(r'resultDate">(.*?)</div>', block, re.S)
        if not date_m:
            continue
        date = parse_date(re.sub(r"<[^>]+>", " ", date_m.group(1)))
        if not date:
            continue
        for sess_m in re.finditer(
            r'<strong>(Morning|Midday|Evening)</strong>.*?<ul class="balls[^"]*">(.*?)</ul>',
            block, re.S,
        ):
            session, sess_block = sess_m.groups()
            nums = [int(x) for x in re.findall(rf'class="{re.escape(ball_class)} number-part-\d+">(\d+)<', sess_block)]
            if not nums:
                continue
            wb_m = re.search(rf'class="{re.escape(ball_class)} wild-ball">(\d+)<', sess_block)
            entry = {"draw_date": date, "session": session.lower(), "numbers": nums}
            if wb_m:
                entry["wild_ball"] = int(wb_m.group(1))
            entries.append(entry)
        # Only scan the first date block's worth of content per split chunk;
        # re.split already isolates one date per chunk via the lookahead.
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
    for e in new_entries:
        e["fetched_at"] = datetime.now(timezone.utc).isoformat()
        e["source_url"] = "https://www.lottery.net/"
        merged[key(e)] = e

    out = sorted(merged.values(), key=lambda e: (e["draw_date"], e.get("session") or ""), reverse=True)[:MAX_HISTORY]
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    log.info("saved %s (%d entries)", path, len(out))


def main():
    try:
        html = fetch("cash")
        entries = extract_single_session(html, "tn-cash", has_bonus=True)
        if entries:
            save("cash", entries, ["draw_date"])
    except Exception as e:
        log.error("tn cash failed: %s", e)
    time.sleep(1)

    try:
        html = fetch("daily-jackpot")
        entries = extract_single_session(html, "tn-daily-jackpot", has_bonus=False)
        if entries:
            save("daily-jackpot", entries, ["draw_date"])
    except Exception as e:
        log.error("tn daily-jackpot failed: %s", e)
    time.sleep(1)

    try:
        html = fetch("cash-3")
        entries = extract_multi_session(html, "tn-cash-3")
        if entries:
            save("cash3", entries, ["draw_date", "session"])
    except Exception as e:
        log.error("tn cash-3 failed: %s", e)
    time.sleep(1)

    try:
        html = fetch("cash-4")
        entries = extract_multi_session(html, "tn-cash-4")
        if entries:
            save("cash4", entries, ["draw_date", "session"])
    except Exception as e:
        log.error("tn cash-4 failed: %s", e)


if __name__ == "__main__":
    main()
