"""
North Carolina own-games — sourced from lotteryusa.com. nclottery.com's own site confirmed blocked
(403 on every plain request with a standard browser UA, same WAF-block class as Montana/Tennessee/
Oklahoma/Pennsylvania before it) — this project's established fallback precedent for that block
class (see `mt_scraper.py`'s module doc). Browser automation was explicitly ruled out project-wide
as impractical for this pipeline.

Confirmed mechanics:
  - Cash 5: 5 of 1-43, $1/play, daily ~11:22pm ET. Has a real "Double Play" feature (separate $1
    add-on, second drawing from a second machine, same 5/43 pool, fixed $50,000 top prize) —
    modeled as a fully separate game `nc-cash5-double-play`, same pattern as Powerball Double Play /
    New Jersey's Pick-6 Double Play. Optional EZ Match instant add-on is NOT modeled (same
    "can't compute without a purchase-time random value we don't have" class as other states' EZ
    Match/Multiplier add-ons).
  - Pick 3 / Pick 4: standard digit games, Midday 3:00pm / Evening ~11:22pm ET, each with a
    Fireball add-on digit (same mechanic as NJ/IL/CT/IN/MO).
  - Cash Pop: 5 named sessions/day — Morning Buzz 9:00am, Lunch Rush 1:00pm, Clock Out Cash 5:00pm,
    Primetime Pop 8:00pm, Midnight Money 11:59pm ET (all confirmed via web search, within this
    project's standing "≤10 draws/day" cutoff) — genuinely the same few-session-per-day mechanic as
    Indiana/Maine/Maryland/Mississippi/Missouri's Cash POP, unlike New Jersey's own differently-
    shaped continuous "Cash Pop" (verify-per-state, don't assume from the name, paid off again).
    Reuses the existing `membershipPick` design directly.

Deliberately NOT built / out of scope:
  - "Lucky 4 Life" (NC's own branding for the multi-state Lucky for Life game) — Lucky for Life
    itself isn't one of this project's 7 already-built multi-state games; adding a brand-new
    multi-state game is out of scope for this per-state "own games" batch.
"""
import datetime as dt
import json
import logging
import time
from pathlib import Path

import lotteryusa_shared as lu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nc_scraper")

OUTPUT_DIR = Path("data") / "north-carolina"
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
            "draw_date": c["draw_date"], "digits": digits, "fireball": c["bonus_number"],
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
        })
    return out


def scrape_cash5() -> tuple[list[dict], list[dict]]:
    url = "https://www.lotteryusa.com/north-carolina/cash-5/"
    cards = lu.parse_draw_cards(lu.fetch_html(url), max_entries=MAX_HISTORY)
    main_out, dp_out = [], []
    for c in cards:
        main_numbers = c["boxes"].get("Main draw") or c["boxes"].get("")
        dp_numbers = c["boxes"].get("Double Play")
        if main_numbers and len(main_numbers) == 5:
            main_out.append({
                "draw_date": c["draw_date"], "numbers": main_numbers,
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
            })
        if dp_numbers and len(dp_numbers) == 5:
            dp_out.append({
                "draw_date": c["draw_date"], "numbers": dp_numbers,
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
            })
    return main_out, dp_out


def scrape_cash_pop(url: str) -> list[dict]:
    cards = lu.parse_draw_cards(lu.fetch_html(url), max_entries=MAX_HISTORY)
    out = []
    for c in cards:
        numbers = next(iter(c["boxes"].values()), [])
        if len(numbers) != 1:
            continue
        out.append({
            "draw_date": c["draw_date"], "number": numbers[0],
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
        })
    return out


def main():
    errors = []

    jobs = [
        ("pick3-midday", lambda: scrape_digit_game("https://www.lotteryusa.com/north-carolina/midday-3/", 3)),
        ("pick3-evening", lambda: scrape_digit_game("https://www.lotteryusa.com/north-carolina/pick-3/", 3)),
        ("pick4-midday", lambda: scrape_digit_game("https://www.lotteryusa.com/north-carolina/midday-pick-4/", 4)),
        ("pick4-evening", lambda: scrape_digit_game("https://www.lotteryusa.com/north-carolina/pick-4/", 4)),
        ("cash-pop-morning-buzz", lambda: scrape_cash_pop("https://www.lotteryusa.com/north-carolina/cash-pop-morning-buzz/")),
        ("cash-pop-lunch-rush", lambda: scrape_cash_pop("https://www.lotteryusa.com/north-carolina/cash-pop-lunch-rush/")),
        ("cash-pop-clock-out-cash", lambda: scrape_cash_pop("https://www.lotteryusa.com/north-carolina/cash-pop-clock-out-cash/")),
        ("cash-pop-primetime-pop", lambda: scrape_cash_pop("https://www.lotteryusa.com/north-carolina/cash-pop-primetime-pop/")),
        ("cash-pop-midnight-money", lambda: scrape_cash_pop("https://www.lotteryusa.com/north-carolina/cash-pop-midnight-money/")),
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

    try:
        main_entries, dp_entries = scrape_cash5()
        if not main_entries:
            raise ValueError("没抓到任何记录")
        save_results("cash5", main_entries)
        log.info("cash5: 最新一条 %s", main_entries[0])
        if dp_entries:
            save_results("cash5-double-play", dp_entries)
            log.info("cash5-double-play: 最新一条 %s", dp_entries[0])
        else:
            log.warning("cash5-double-play: 没抓到记录")
    except Exception as e:  # noqa: BLE001
        log.error("cash5 失败: %s", e)
        errors.append(("cash5", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
