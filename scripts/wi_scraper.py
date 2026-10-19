"""
Wisconsin own-games — sourced from lotteryusa.com, per explicit user direction this session
("用替代的" - use the substitute). wilottery.com's official site is TLS/WAF-blocked from this
project's scraping environment (SSL_ERROR_SYSCALL / SSLEOFError on every plain request, same class
of block as Montana/Tennessee/Oklahoma/Pennsylvania/North Carolina before it) - confirmed this
session, not assumed.

Confirmed mechanics (lotteryusa.com/wisconsin/ nav + each game's own page, re-verified this
session):
  - Daily Pick 3 / Daily Pick 4: midday + evening sessions each, standard digit games, no
    Fireball-style bonus digit shown on either lotteryusa.com page (unlike some other states'
    Pick 3/4 - verified per-state, not assumed from the name).
  - Badger 5: 5 of 1-31, in-state pari-mutuel jackpot (real jackpot seen this session: $223,000).
  - Super Cash!: 6 of 1-39, in-state pari-mutuel jackpot (real jackpot seen this session: $350,000).
  - Very Own Megabucks (WI's branding for its own in-state Megabucks, NOT the old multi-state
    game of the same generic name): 6 of 1-49, Wed/Sat draws, real jackpot seen this session:
    $3 Million.

Deliberately NOT built / out of scope:
  - All or Nothing (midday + evening): draws 11 numbers from 1-22 with an "all or nothing" prize
    mechanic (win by matching ALL 11 or NONE of them) - a genuinely different match shape than
    every existing `MatchRule.Kind` in this project; needs its own dedicated design pass rather
    than being rushed into this batch.

Exact ticket prices and full payout tables are NOT confirmed from lotteryusa.com (it shows only
the current jackpot/top-prize figure, not the full per-tier prize chart) - the Swift-side config
should carry the project's standard default pick-game prize table with a payoutNote flagging it as
unconfirmed, same convention already used for other states pending their own published figures
(see e.g. `mi-daily3`).
"""
import datetime as dt
import json
import logging
import time
from pathlib import Path

import lotteryusa_shared as lu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wi_scraper")

OUTPUT_DIR = Path("data") / "wisconsin"
MAX_HISTORY = 100


def save_results(subdir: str, new_entries: list[dict]) -> Path:
    out_dir = OUTPUT_DIR / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "latest.json"
    existing: list[dict] = []
    if out_path.exists():
        try:
            loaded = json.loads(out_path.read_text())
            if isinstance(loaded, list):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = []
    merged = lu.dedup_merge(existing, new_entries, "draw_date", MAX_HISTORY)
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("saved %s (%d entries)", out_path, len(merged))
    return out_path


def scrape_digit_game(url: str, digit_count: int) -> list[dict]:
    cards = lu.parse_draw_cards(lu.fetch_html(url), max_entries=MAX_HISTORY)
    out = []
    for c in cards:
        digits = next(iter(c["boxes"].values()), [])
        if len(digits) != digit_count:
            continue
        out.append({
            "draw_date": c["draw_date"], "digits": digits,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
        })
    return out


def scrape_numbers_game(url: str, count: int) -> list[dict]:
    cards = lu.parse_draw_cards(lu.fetch_html(url), max_entries=MAX_HISTORY)
    out = []
    for c in cards:
        numbers = next(iter(c["boxes"].values()), [])
        if len(numbers) != count:
            continue
        out.append({
            "draw_date": c["draw_date"], "numbers": numbers,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
        })
    return out


def main():
    errors = []

    jobs = [
        ("pick3-midday", lambda: scrape_digit_game("https://www.lotteryusa.com/wisconsin/daily-pick-3/", 3)),
        ("pick3-evening", lambda: scrape_digit_game("https://www.lotteryusa.com/wisconsin/daily-pick-3-evening/", 3)),
        ("pick4-midday", lambda: scrape_digit_game("https://www.lotteryusa.com/wisconsin/daily-pick-4/", 4)),
        ("pick4-evening", lambda: scrape_digit_game("https://www.lotteryusa.com/wisconsin/daily-pick-4-evening/", 4)),
        ("badger-5", lambda: scrape_numbers_game("https://www.lotteryusa.com/wisconsin/badger-5/", 5)),
        ("super-cash", lambda: scrape_numbers_game("https://www.lotteryusa.com/wisconsin/super-cash/", 6)),
        ("megabucks", lambda: scrape_numbers_game("https://www.lotteryusa.com/wisconsin/very-own-megabucks/", 6)),
    ]
    for subdir, fn in jobs:
        try:
            entries = fn()
            if not entries:
                raise ValueError("no entries scraped")
            save_results(subdir, entries)
            log.info("%s: latest %s", subdir, entries[0])
        except Exception as e:  # noqa: BLE001
            log.error("%s failed: %s", subdir, e)
            errors.append((subdir, e))
        time.sleep(1)

    if errors:
        raise RuntimeError(f"{len(errors)} game(s) failed: {errors}")


if __name__ == "__main__":
    main()
