"""
New Jersey own-games — sourced from lotteryusa.com (see `lotteryusa_shared.py`'s module doc for why:
njlottery.com's own winning-numbers pages render client-side via an Underscore.js template with no
plain-HTTP-visible JSON source found this round).

Confirmed mechanics:
  - Pick-3 / Pick-4: standard digit games, Midday ~12:59pm / Evening ~10:57pm ET, each with a
    Fireball add-on digit (same substitute-one-drawn-digit mechanic as IL/CT/IN/MO's own-named
    versions) — confirmed via lotteryusa.com's `.c-result__bonus` abbr title="Fireball".
  - Jersey Cash 5: 5 of 1-45, $2/play, daily ~10:57pm ET. BULLSEYE is literally the 5th (last) of
    the 5 drawn numbers, randomly flagged — NOT a separately-drawn 6th number (confirmed via
    lotteryusa.com's own abbr title="Bullseye" rendering showing it as one of the ball-box's balls,
    and via lotteryusa.com's own news writeup) — same `MatchRule.Kind.bullseyeMatch` mechanic this
    project already built for Missouri's MO Millions, reused directly, not a new Kind.
    Optional XTRA ($1, up to 5x non-jackpot prizes) is NOT modeled (same "can't compute without a
    purchase-time random value we don't have" class as MS Match5's Multiplier/MO Millions' EZ Match).
  - Pick-6: 6 of 1-46, $2/play, Mon/Thu/Sat ~10:57pm ET. Has its own real "Double Play" feature
    (separate $1 add-on, second drawing from a second machine, same 6/46 pool, fixed $250,000 top
    prize pari-mutuel) — modeled as a fully separate game `nj-pick6-double-play`, same established
    pattern as Powerball Double Play / North Carolina's Cash 5 Double Play. Optional XTRA on Pick-6
    (and on Double Play) also left unmodeled, same reasoning as above.

Deliberately NOT built (both confirmed >10 draws/day from this project's own research, via
njlottery.com's own copy and independent web search):
  - Quick Draw: every 4 minutes, 300+ draws/day.
  - Cash Pop: NJ's own Cash Pop is ALSO a continuous every-4-minutes "monitor game" (5:06am-1:34am,
    hundreds/day) — genuinely different from Indiana/Maine/Maryland/Mississippi/Missouri's few-
    session-per-day Cash POP despite the identical name. Confirms this project's standing rule to
    verify each state's real frequency per-game rather than assuming from the name (same lesson as
    Kentucky's differently-shaped "CASHPOP").
"""
import datetime as dt
import json
import logging
import time
from pathlib import Path

import lotteryusa_shared as lu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nj_scraper")

OUTPUT_DIR = Path("data") / "new-jersey"
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


def scrape_digit_game(url: str, game_key: str, digit_count: int) -> list[dict]:
    """Pick-3/Pick-4: one box per row (no 'Midday'/'Evening' title on lotteryusa.com's page — this
    scraper is called once per game and relies on there being exactly one row per calendar day; NJ
    actually draws twice daily, so lotteryusa.com's per-day row is the EVENING draw unless the URL
    explicitly says otherwise. Confirmed: lotteryusa.com splits NJ's two Pick-3/4 sessions onto two
    separate URLs (`/pick-3/` = evening, `/pick-3-midday/` = midday), same convention used for every
    other two-session state's lotteryusa.com fallback in this project."""
    cards = lu.parse_draw_cards(lu.fetch_html(url), max_entries=MAX_HISTORY)
    out = []
    for c in cards:
        digits = next(iter(c["boxes"].values()), [])
        if len(digits) != digit_count:
            continue
        out.append({
            "draw_date": c["draw_date"],
            "digits": digits,
            "fireball": c["bonus_number"],
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": url,
        })
    return out


def scrape_cash5() -> list[dict]:
    url = "https://www.lotteryusa.com/new-jersey/cash-5/"
    cards = lu.parse_draw_cards(lu.fetch_html(url), max_entries=MAX_HISTORY)
    out = []
    for c in cards:
        numbers = next(iter(c["boxes"].values()), [])
        if len(numbers) != 4:  # lotteryusa renders the Bullseye ball separately; main box has 4
            continue
        out.append({
            "draw_date": c["draw_date"],
            "numbers": numbers,
            "bullseye_number": c["bonus_number"],
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": url,
        })
    return out


def scrape_pick6() -> tuple[list[dict], list[dict]]:
    url = "https://www.lotteryusa.com/new-jersey/pick-6/"
    cards = lu.parse_draw_cards(lu.fetch_html(url), max_entries=MAX_HISTORY)
    main_out, dp_out = [], []
    for c in cards:
        main_numbers = c["boxes"].get("Main draw") or c["boxes"].get("")
        dp_numbers = c["boxes"].get("Double Play")
        if main_numbers and len(main_numbers) == 6:
            main_out.append({
                "draw_date": c["draw_date"], "numbers": main_numbers,
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
            })
        if dp_numbers and len(dp_numbers) == 6:
            dp_out.append({
                "draw_date": c["draw_date"], "numbers": dp_numbers,
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
            })
    return main_out, dp_out


def main():
    errors = []

    jobs = [
        ("pick3-midday", lambda: scrape_digit_game("https://www.lotteryusa.com/new-jersey/midday-pick-3/", "pick3", 3)),
        ("pick3-evening", lambda: scrape_digit_game("https://www.lotteryusa.com/new-jersey/pick-3/", "pick3", 3)),
        ("pick4-midday", lambda: scrape_digit_game("https://www.lotteryusa.com/new-jersey/midday-pick-4/", "pick4", 4)),
        ("pick4-evening", lambda: scrape_digit_game("https://www.lotteryusa.com/new-jersey/pick-4/", "pick4", 4)),
        ("cash5", scrape_cash5),
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
        main_entries, dp_entries = scrape_pick6()
        if not main_entries:
            raise ValueError("没抓到任何记录")
        save_results("pick6", main_entries)
        log.info("pick6: 最新一条 %s", main_entries[0])
        if dp_entries:
            save_results("pick6-double-play", dp_entries)
            log.info("pick6-double-play: 最新一条 %s", dp_entries[0])
        else:
            log.warning("pick6-double-play: 没抓到记录（可能当期没买 Double Play 的页面渲染）")
    except Exception as e:  # noqa: BLE001
        log.error("pick6 失败: %s", e)
        errors.append(("pick6", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
