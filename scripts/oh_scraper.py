"""
Ohio own-games — sourced from lotteryusa.com. ohiolottery.com's own winning-numbers pages return
200 (not Cloudflare/WAF-blocked like North Carolina/Montana), but render results client-side with
no plain-HTTP-visible JSON/API source found within this round's research budget — a pragmatic
fallback choice, not a confirmed-block precedent. Worth re-investigating the real API later.

Confirmed mechanics:
  - Classic Lotto: 6 of 1-49, $1/play, Mon/Wed/Sat ~7:05pm ET. Has an optional "Kicker" add-on (a
    separately-purchased $1 ticket printing 6 digits, compared position-by-position left-to-right
    against a drawn 6-digit string — e.g. lotteryusa.com renders "Kicker: 552976") — this is a
    genuinely different mechanic from every other add-on already modeled in this project
    (Fireball-style digit substitution, Bullseye flagged-ball, Power Play multiplier) and was NOT
    built this round given the scope of this batch; left unmodeled, same "optional secondary
    add-on, documented gap" treatment as MS Match5's Multiplier/MO Millions' EZ Match/NJ's XTRA.
  - Pick 3 / Pick 4 / Pick 5: standard digit games, Midday 12:29pm / Evening ~7:29pm ET (Saturday
    evening ~7:45pm per Ohio's own schedule page, not separately modeled here — the small schedule
    variance on one weekday isn't worth a second GAMES entry; the scraper itself doesn't care about
    exact draw time). No Fireball-equivalent add-on confirmed for Ohio's digit games (0 Fireball
    mentions on lotteryusa.com's OH pick-3/4/5 pages, checked directly).
  - Rolling Cash 5: 5 of 1-39, $1/play, daily ~7:05pm ET, no special ball.

Deliberately NOT built: KENO (confirmed ~4-minute draws, 6:04am-2:28am, ~245/day — far over this
project's standing "≤10 draws/day" cutoff, same class as Missouri's Club Keno).
"""
import datetime as dt
import json
import logging
import time
from pathlib import Path

import lotteryusa_shared as lu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("oh_scraper")

OUTPUT_DIR = Path("data") / "ohio"
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
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
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


def scrape_pool_game(url: str, main_count: int) -> list[dict]:
    cards = lu.parse_draw_cards(lu.fetch_html(url), max_entries=MAX_HISTORY)
    out = []
    for c in cards:
        numbers = next(iter(c["boxes"].values()), [])
        if len(numbers) != main_count:
            continue
        out.append({
            "draw_date": c["draw_date"], "numbers": numbers,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
        })
    return out


def main():
    errors = []
    jobs = [
        ("classic-lotto", lambda: scrape_pool_game("https://www.lotteryusa.com/ohio/classic-lotto/", 6)),
        ("pick3-midday", lambda: scrape_digit_game("https://www.lotteryusa.com/ohio/midday-pick-3/", 3)),
        ("pick3-evening", lambda: scrape_digit_game("https://www.lotteryusa.com/ohio/pick-3/", 3)),
        ("pick4-midday", lambda: scrape_digit_game("https://www.lotteryusa.com/ohio/midday-pick-4/", 4)),
        ("pick4-evening", lambda: scrape_digit_game("https://www.lotteryusa.com/ohio/pick-4/", 4)),
        ("pick5-midday", lambda: scrape_digit_game("https://www.lotteryusa.com/ohio/midday-pick-5/", 5)),
        ("pick5-evening", lambda: scrape_digit_game("https://www.lotteryusa.com/ohio/pick-5/", 5)),
        ("rolling-cash5", lambda: scrape_pool_game("https://www.lotteryusa.com/ohio/rolling-cash-5/", 5)),
    ]
    for subdir, fn in jobs:
        try:
            entries = fn()
            if not entries:
                raise ValueError("没抓到任何记录")
            save_results(subdir, entries)
            log.info("%s: 最新一条 %s", subdir, entries[0])
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))
        time.sleep(1)

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
