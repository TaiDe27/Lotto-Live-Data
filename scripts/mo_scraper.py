"""
Missouri own-games — molottery.com's real pages live at a `.jsp`/`.do` path shape, NOT the
`/mo-millions/`-style trailing-slash path the site's own robots.txt superficially suggests (that
bare path 403s — Apache-level, not a Cloudflare JS challenge — while `/mo-millions/mo-millions.jsp`
and `/mo-millions/winning-numbers.do` both 200 with a plain browser UA; found by reading the site's
own internal nav links rather than guessing the URL shape). Both are Drupal-rendered server-side —
no JS execution needed.

Confirmed mechanics (see CLAUDE.md's "one-off per-game quirks" section for where this is modeled in
Swift — `MatchRule.Kind.bullseyeMatch`):
  - MO Millions: 6 of 1-42, $2/play, Wed/Sat ~8:59pm CT. Each drawing, ONE of the 6 drawn numbers is
    randomly flagged "Bulls-Eye" (rendered with an extra `bullseye` CSS class on that one ball) —
    NOT a 7th independently-drawn number. A "Double Play Drawing" (its own separate 6 numbers + its
    own separate Bulls-Eye flag, same page) is modeled as a fully separate game
    ("mo-millions-double-play"), same pattern as Powerball/Powerball Double Play.
  - Show Me Cash: 5 of 1-39, $1/play, daily ~8:59pm CT, no special ball.
  - Pick 3 / Pick 4: standard digit games, Midday ~12:45pm / Evening ~8:59pm CT, each session also
    draws a "Wild Ball" add-on digit (molottery.com's own name for the same substitute-one-drawn-
    digit mechanic as Illinois' Fireball/Connecticut's Wild Ball/Indiana's Superball).
  - Cash Pop: 5 sessions/day (Early Bird 8am, Late Morning 11am, Matinee 3pm, Prime Time 7pm, Night
    Owl 11pm CT) — reuses the Indiana/Maine/Maryland Cash POP membership-pick design directly.

Club Keno (4-minute draws, confirmed from molottery.com's own game rules page: "drawings ... take
place at four-minute intervals, daily from 5:04 a.m. to 1:52 a.m.") is deliberately NOT built —
~260 draws/day, far over this project's standing "≤10 draws/day" cutoff.
"""
import dataclasses
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mo_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "missouri"
MAX_HISTORY = 100


def _get(url: str):
    import requests
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def _parse_date_title(title: str, year_hint: int) -> str:
    """e.g. 'Wednesday, Oct 14' -> '2026-10-14' (no year on the page; anchor to today's year, or
    the previous year if the parsed month/day would otherwise be > 1 day in the future — handles
    the Dec 31 -> Jan 1 rollover)."""
    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2})$", title.strip())
    if not m:
        raise ValueError(f"日期格式不对: {title!r}")
    month_name, day = m.group(1), int(m.group(2))
    month = dt.datetime.strptime(month_name[:3], "%b").month
    candidate = dt.date(year_hint, month, day)
    today = dt.datetime.now(dt.timezone.utc).date()
    if (candidate - today).days > 1:
        candidate = dt.date(year_hint - 1, month, day)
    return candidate.isoformat()


def save_results(subdir: str, new_entries: list[dict], key_fields: tuple[str, ...]) -> Path:
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
    new_keys = {tuple(e.get(k) for k in key_fields) for e in new_entries}
    history = [h for h in history if tuple(h.get(k) for k in key_fields) not in new_keys]
    merged = (new_entries + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


# ---------------------------------------------------------------------------
# MO Millions (+ Double Play, + Bulls-Eye)
# ---------------------------------------------------------------------------

def fetch_mo_millions() -> dict:
    from bs4 import BeautifulSoup

    html = _get("https://www.molottery.com/mo-millions/winning-numbers.do")
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(".game-single-calendar__item")
    if len(items) < 2:
        raise ValueError(f"应至少有 Main Drawing + Double Play Drawing 两个区块，抓到 {len(items)} 个")

    today_year = dt.datetime.now(dt.timezone.utc).year
    main_item, double_play_item = items[0], items[1]

    def parse_numbers(item):
        nums = []
        bullseye = None
        for num_el in item.select(".game-single-calendar__body .game-single-calendar__num"):
            value = int(num_el.get_text(strip=True))
            nums.append(value)
            if "bullseye" in num_el.get("class", []):
                bullseye = value
        if len(nums) != 6:
            raise ValueError(f"应为6个号码，抓到 {len(nums)} 个: {nums}")
        if bullseye is None:
            raise ValueError("没找到 bullseye 号码")
        return nums, bullseye

    # Only the Main Drawing block prints its own date ("Wednesday, Oct 14" + "Main Drawing") — the
    # Double Play Drawing block (same page, right below it) has no date title of its own since it's
    # always the same drawing date as the Main Drawing above it.
    main_titles = [t.get_text(strip=True) for t in main_item.select(".game-single-calendar__title")]
    date_title = next((t for t in main_titles if re.search(r"[A-Za-z]+,\s*[A-Za-z]{3,9}\s+\d{1,2}", t)), None)
    if date_title is None:
        raise ValueError(f"没找到日期标题: {main_titles}")
    main_date = _parse_date_title(date_title, today_year)
    dp_date = main_date

    main_nums, main_bullseye = parse_numbers(main_item)
    dp_nums, dp_bullseye = parse_numbers(double_play_item)

    next_jackpot = None
    bottom = soup.select_one(".game-single-calendar__bottom")
    if bottom:
        m = re.search(r"\$([\d.]+)\s*(Million|Thousand)?", bottom.get_text())
        if m:
            value = float(m.group(1))
            unit = (m.group(2) or "").lower()
            if unit == "million":
                value *= 1_000_000
            elif unit == "thousand":
                value *= 1_000
            next_jackpot = int(value)

    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    return {
        "draw_date": main_date,
        "numbers": main_nums,
        "bullseye_number": main_bullseye,
        "double_play_draw_date": dp_date,
        "double_play_numbers": dp_nums,
        "double_play_bullseye_number": dp_bullseye,
        "next_jackpot_amount": next_jackpot,
        "fetched_at": fetched_at,
        "source_url": "https://www.molottery.com/mo-millions/winning-numbers.do",
    }


# ---------------------------------------------------------------------------
# Show Me Cash
# ---------------------------------------------------------------------------

def fetch_show_me_cash() -> dict:
    from bs4 import BeautifulSoup

    html = _get("https://www.molottery.com/show-me-cash/winning-numbers.do")
    soup = BeautifulSoup(html, "html.parser")
    item = soup.select_one(".game-single-calendar__item")
    if item is None:
        raise ValueError("没找到 .game-single-calendar__item")

    title = item.select_one(".game-single-calendar__title")
    if title is None:
        raise ValueError("没找到日期标题")
    draw_date = _parse_date_title(title.get_text(strip=True), dt.datetime.now(dt.timezone.utc).year)

    nums = [int(n.get_text(strip=True)) for n in item.select(".game-single-calendar__num")]
    if len(nums) != 5:
        raise ValueError(f"应为5个号码，抓到 {len(nums)} 个: {nums}")

    return {
        "draw_date": draw_date,
        "numbers": nums,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_url": "https://www.molottery.com/show-me-cash/winning-numbers.do",
    }


# ---------------------------------------------------------------------------
# Pick 3 / Pick 4 (with Wild Ball)
# ---------------------------------------------------------------------------

def fetch_pick(game: str, digit_count: int) -> list[dict]:
    from bs4 import BeautifulSoup

    url = f"https://www.molottery.com/{game}/winning-numbers.do"
    html = _get(url)
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(".game-single-calendar__item")
    if not items:
        raise ValueError("没找到 .game-single-calendar__item")

    today_year = dt.datetime.now(dt.timezone.utc).year
    date_title_el = soup.select_one(".game-single-calendar__title_mbottom")
    if date_title_el is None:
        raise ValueError("没找到 .game-single-calendar__title_mbottom 开奖日期")
    draw_date = _parse_date_title(date_title_el.get_text(strip=True), today_year)

    results = []
    for item in items:
        title_el = item.select_one(".game-single-calendar__title")
        if title_el is None:
            continue
        session_text = title_el.get_text(strip=True).lower()
        session = "evening" if "evening" in session_text else ("midday" if "midday" in session_text else None)
        if session is None:
            continue

        digits = [int(t) for n in item.select(".game-single-calendar__num") if (t := n.get_text(strip=True)).isdigit()]
        # This item's digits not fully posted yet (placeholder text instead of real numbers) is
        # expected for whichever session hasn't drawn today — skip just this one item rather than
        # failing the whole fetch, same fix as pa_scraper.py/wi_scraper.py 2026-10-23.
        if len(digits) != digit_count:
            log.info("[%s] %s 还没开奖（预期%d位数字，抓到%d个），正常现象", game, session, digit_count, len(digits))
            continue

        wild_ball = None
        wild_el = item.select_one(".game-single-calendar__wild span")
        if wild_el:
            wild_text = wild_el.get_text(strip=True)
            wild_ball = int(wild_text) if wild_text.isdigit() else None

        results.append({
            "draw_date": draw_date,
            "session": session,
            "digits": digits,
            "wild_ball": wild_ball,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": url,
        })
    if not results:
        raise ValueError("解析出 0 条结果")
    return results


# ---------------------------------------------------------------------------
# Cash Pop (5 sessions/day)
# ---------------------------------------------------------------------------

SESSION_LABEL_MAP = {
    "early bird": "early_bird",
    "late morning": "late_morning",
    "matinee": "matinee",
    "prime time": "prime_time",
    "night owl": "night_owl",
}


def fetch_cash_pop() -> list[dict]:
    from bs4 import BeautifulSoup

    url = "https://www.molottery.com/cash-pop/winning-numbers.do"
    html = _get(url)
    soup = BeautifulSoup(html, "html.parser")
    item = soup.select_one(".game-single-calendar__item")
    if item is None:
        raise ValueError("没找到 .game-single-calendar__item")

    date_title = item.select_one(".game-single-calendar__title_small")
    if date_title is None:
        raise ValueError("没找到日期标题")
    draw_date = _parse_date_title(date_title.get_text(strip=True), dt.datetime.now(dt.timezone.utc).year)

    results = []
    # The main (Night Owl) title+number is directly in `item`; the other 4 sessions are each in
    # their own `.game-single-calendar__column`.
    main_title = item.select_one(".game-single-calendar__title:not(.game-single-calendar__title_small)")
    if main_title and main_title.find("span"):
        span_text = main_title.find("span").get_text(strip=True)
        label = main_title.get_text(strip=True).split(span_text)[0].strip().lower()
        session = SESSION_LABEL_MAP.get(label)
        if session and span_text.isdigit():
            results.append((session, int(span_text)))

    for col in item.select(".game-single-calendar__column"):
        title = col.select_one(".game-single-calendar__title:not(.game-single-calendar__title_small)")
        if title is None or title.find("span") is None:
            continue
        span_text = title.find("span").get_text(strip=True)
        label = title.get_text(strip=True).split(span_text)[0].strip().lower()
        session = SESSION_LABEL_MAP.get(label)
        if session and span_text.isdigit():
            results.append((session, int(span_text)))

    # Cash Pop's 5 sessions post incrementally through the day - fewer than 5 is expected outside
    # the ~11pm window, not a failure (same fix class as pa_scraper.py 2026-10-23). Only 0 is a
    # genuine problem.
    if len(results) == 0:
        raise ValueError("Cash Pop: 一个场次都没抓到")
    if len(results) < 5:
        log.info("Cash Pop: 只抓到 %d 个场次（%s），其余大概率还没开奖，正常现象",
                  len(results), [r[0] for r in results])

    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    return [
        # `numbers` wraps the single drawn number in a 1-element array, matching every other Cash
        # POP state's scraper convention (`RemoteGameConfigInterpreter.intArray`'s numeric shape
        # expects an array field, not a bare scalar).
        {"draw_date": draw_date, "session": session, "numbers": [number], "fetched_at": fetched_at, "source_url": url}
        for session, number in results
    ]


def main():
    errors = []

    try:
        result = fetch_mo_millions()
        log.info("mo-millions: %s", result)
        save_results("mo-millions", [result], key_fields=("draw_date",))
    except Exception as e:  # noqa: BLE001
        log.error("mo-millions 失败: %s", e)
        errors.append(("mo-millions", e))

    try:
        result = fetch_show_me_cash()
        log.info("show-me-cash: %s", result)
        save_results("show-me-cash", [result], key_fields=("draw_date",))
    except Exception as e:  # noqa: BLE001
        log.error("show-me-cash 失败: %s", e)
        errors.append(("show-me-cash", e))

    for game, subdir, digit_count in [("pick3", "pick3", 3), ("pick4", "pick4", 4)]:
        try:
            results = fetch_pick(game, digit_count)
            log.info("%s: %s", subdir, results)
            save_results(subdir, results, key_fields=("draw_date", "session"))
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))

    try:
        results = fetch_cash_pop()
        log.info("cash-pop: %s", results)
        save_results("cash-pop", results, key_fields=("draw_date", "session"))
    except Exception as e:  # noqa: BLE001
        log.error("cash-pop 失败: %s", e)
        errors.append(("cash-pop", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
