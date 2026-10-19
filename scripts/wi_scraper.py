"""
Wisconsin own-games — migrated 2026-10-19 from lotteryusa.com to lottery.net entirely, per explicit
user direction (asked whether another state had used a different third-party substitute than
lotteryusa.com - Pennsylvania/Tennessee already had, via lottery.net). wilottery.com's official
site is still confirmed TLS/WAF-blocked from this project's scraping environment
(SSL_ERROR_SYSCALL / SSLEOFError on every plain request, same class of block as Montana/Tennessee/
Oklahoma/Pennsylvania/North Carolina before it) - this is still a third-party source, not the
official site, same class of fallback as those states' lotteryusa.com usage, just a different
aggregator for Wisconsin specifically.

Why migrate off lotteryusa.com rather than leave it: lottery.net's single `/wisconsin/pick-N/
numbers` page shows BOTH Midday and Evening sessions for the same date in one fetch (two
`.drawTime` blocks, labelled by clock time - "1:00 PM"/"9:00 PM" - rather than Pennsylvania's
literal "Day"/"Evening" labels), where lotteryusa.com needed two separate URLs; and lottery.net's
jackpot-game pages (Badger 5/Super Cash!/Megabucks) expose the real current jackpot amount
directly, which lotteryusa.com's pages didn't surface. Every number cross-checked against this
session's earlier lotteryusa.com-sourced scrape and matched exactly (same real draws).

Confirmed real mechanics (quoted from lottery.net's own Wisconsin game pages, re-verified
independently of the prior lotteryusa.com-sourced quotes, not just carried over):
  - Pick 3/4: "Midday game at around 1:30 PM CT and an Evening edition at 9:00 PM CT." No
    Wild-Ball-style bonus digit shown on either session (confirmed absent, same finding as the
    prior lotteryusa.com-sourced pass - not assumed from the name).
  - Badger 5: 5 of 1-31, daily 9:00 PM CT, in-state pari-mutuel jackpot.
  - Super Cash!: 6 of 1-39, daily 9:00 PM CT. The results table's 7th ball position is a literal
    "No"/"Yes" Doubler-feature indicator, NOT a 7th drawn number - filtered out, not modeled (same
    "can't compute without a purchase-time random value" class as other states' EZ Match/Doubler
    add-ons).
  - Very Own Megabucks: 6 of 1-49, Wed/Sat only, in-state jackpot (WI's own branding - distinct
    from any multi-state game of a similar name).

Deliberately NOT built / out of scope (unchanged from the prior pass): All or Nothing (midday +
evening) - draws 11 numbers from 1-22 with an "all or nothing" prize mechanic, a genuinely
different match shape than every existing `MatchRule.Kind`; needs its own dedicated design pass.
"""
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wi_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "wisconsin"
MAX_HISTORY = 100
BASE = "https://www.lottery.net/wisconsin"


def _fetch_soup(url: str):
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("attempt %d failed (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"gave up after retries: {url}") from last_err


def _parse_date(date_text: str) -> str:
    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s*(\d{4})", date_text)
    if not m:
        raise ValueError(f"unparseable date: {date_text!r}")
    month_name, day, year = m.groups()
    month = dt.datetime.strptime(month_name[:3], "%b").month
    return dt.date(int(year), month, int(day)).isoformat()


def _first_result_box(soup):
    box = soup.select_one(".resultBox.firstResult")
    if box is None:
        raise ValueError("no .resultBox.firstResult found")
    return box


def fetch_pool(slug: str, main_count: int) -> dict:
    url = f"{BASE}/{slug}/numbers"
    soup = _fetch_soup(url)
    box = _first_result_box(soup)
    date_el = box.select_one(".resultDate")
    draw_date = _parse_date(date_el.get_text(" ", strip=True))
    balls = box.select("ul.balls li.ball")
    # Super Cash!'s results table has a trailing "No"/"Yes" Doubler-feature indicator, not a real
    # drawn number - keep only genuinely numeric balls.
    numbers = [int(t) for b in balls if (t := b.get_text(strip=True)).isdigit()]
    if len(numbers) != main_count:
        raise ValueError(f"expected {main_count} numbers, got {len(numbers)}: {numbers}")
    return {
        "draw_date": draw_date, "numbers": numbers,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
    }


def fetch_digits_both_sessions(slug: str, digit_count: int) -> list:
    """Pick-N pages show Midday + Evening in ONE fetch (two `.drawTime` blocks, labelled by clock
    time rather than Pennsylvania's literal "Day"/"Evening" text)."""
    url = f"{BASE}/{slug}/numbers"
    soup = _fetch_soup(url)
    box = _first_result_box(soup)
    date_el = box.select_one(".resultDate")
    draw_date = _parse_date(date_el.get_text(" ", strip=True))
    results = []
    for draw_time_el in box.select(".drawTime"):
        label = draw_time_el.select_one("strong")
        if label is None:
            continue
        hour_text = label.get_text(strip=True)  # e.g. "1:00 PM" / "9:00 PM"
        time_match = re.match(r"(\d{1,2}):\d{2}\s*(AM|PM)", hour_text, re.IGNORECASE)
        if not time_match:
            raise ValueError(f"{slug}: unparseable session clock-time label {hour_text!r}")
        hour12, meridiem = int(time_match.group(1)), time_match.group(2).upper()
        hour24 = (hour12 % 12) + (12 if meridiem == "PM" else 0)
        session = "midday" if hour24 < 17 else "evening"
        digit_lis = draw_time_el.select("ul.balls li[class*='number-part']")
        digits = [int(li.get_text(strip=True)) for li in digit_lis]
        if len(digits) != digit_count:
            raise ValueError(f"{slug} {session}: expected {digit_count} digits, got {len(digits)}: {digits}")
        results.append({
            "draw_date": draw_date, "session": session, "digits": digits,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
        })
    if len(results) != 2:
        raise ValueError(f"{slug}: expected 2 sessions (midday/evening), got {len(results)}")
    return results


def save_pool(subdir: str, new_entry: dict) -> Path:
    out_dir = OUTPUT_DIR / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "latest.json"
    history: list = []
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
            if isinstance(existing, list):
                history = existing
        except (json.JSONDecodeError, OSError):
            history = []
    history = [h for h in history if h.get("draw_date") != new_entry["draw_date"]]
    merged = ([new_entry] + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("saved %s (%d entries)", out_path, len(merged))
    return out_path


def save_digits(subdir: str, new_entry: dict) -> Path:
    out_dir = OUTPUT_DIR / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "latest.json"
    history: list = []
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
            if isinstance(existing, list):
                history = existing
        except (json.JSONDecodeError, OSError):
            history = []
    history = [h for h in history if not (h.get("draw_date") == new_entry["draw_date"] and h.get("session") == new_entry["session"])]
    merged = ([new_entry] + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("saved %s (%d entries)", out_path, len(merged))
    return out_path


def main():
    errors = []

    pool_games = [
        ("badger-5", "badger-5", 5),
        ("super-cash", "supercash", 6),
        ("megabucks", "megabucks", 6),
    ]
    for subdir, slug, count in pool_games:
        try:
            result = fetch_pool(slug, count)
            log.info("%s: %s", subdir, result)
            save_pool(subdir, result)
        except Exception as e:  # noqa: BLE001
            log.error("%s failed: %s", subdir, e)
            errors.append((subdir, e))
        time.sleep(1.0)

    digit_games = [("pick3", "pick-3", 3), ("pick4", "pick-4", 4)]
    for base_subdir, slug, count in digit_games:
        try:
            for result in fetch_digits_both_sessions(slug, count):
                subdir = f"{base_subdir}-{result['session']}"
                log.info("%s: %s", subdir, result)
                save_digits(subdir, result)
        except Exception as e:  # noqa: BLE001
            log.error("%s failed: %s", slug, e)
            errors.append((slug, e))
        time.sleep(1.0)

    if errors:
        raise RuntimeError(f"{len(errors)} game(s) failed: {errors}")


if __name__ == "__main__":
    main()
