"""
Pennsylvania own-games — palottery.pa.gov and palottery.state.pa.us both confirmed hard-blocked:
the TLS handshake itself fails (SSL_ERROR_SYSCALL) on every request, more severe than a WAF
challenge — zero bytes obtainable at all. Per explicit user decision, this uses lotteryusa.com
instead, same fallback pattern as Montana/North Carolina/Tennessee/Oklahoma.

Confirmed real mechanics (quoted from lotteryusa.com's own PA game pages):
  - Cash 5: 5 of 1-43, $2/play, daily 6:59 PM ET.
  - Match 6: 6 of 1-49, $2/play, daily 6:59 PM ET.
  - Treasure Hunt: 5 of 1-30, $1/play, daily 1:35 PM ET.
  - Pick 2/3/4/5: standard digit games, each with a Wild Ball add-on digit ("Add Wild Ball for
    greater odds of winning a prize"). Each game has TWO SEPARATE lotteryusa.com URLs, not one
    combined results page: `/pennsylvania/pick-N/` is the EVENING session (6:59 PM ET),
    `/pennsylvania/midday-pick-N/` is the DAY session (1:35 PM ET) — confirmed by actually
    comparing both pages' real numbers for the same date (different results), not assumed from
    the URL naming alone.

Cash Pop (4 named PA sessions: Morning Buzz/Lunch Break/Prime Time/Night Owl) is NOT built this
round — a real, serious attempt to find its official draw times (both the official site, which is
network-blocked, and lotteryusa.com's own 4 session-specific result pages, which turned out to be
pure results archives with no "how to play"/schedule text found) came up empty. Flagged as an open
question rather than guessing the session times.
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
log = logging.getLogger("pa_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "pennsylvania"
MAX_HISTORY = 100


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
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def _parse_date(date_text: str) -> str:
    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})", date_text)
    if not m:
        raise ValueError(f"日期格式不对: {date_text!r}")
    month_name, day, year = m.groups()
    month = dt.datetime.strptime(month_name[:3], "%b").month
    return dt.date(int(year), month, int(day)).isoformat()


def fetch_pool(url: str, main_count: int) -> dict:
    soup = _fetch_soup(url)
    row = soup.select_one(".c-draw-card")
    if row is None:
        raise ValueError("没找到 .c-draw-card")
    date_el = row.select_one(".c-draw-card__draw-date-sub")
    draw_date = _parse_date(date_el.get_text(strip=True))
    balls = row.select("ul.c-draw-card__ball-list li.c-ball")
    numbers = [int(b.get_text(strip=True)) for b in balls]
    if len(numbers) != main_count:
        raise ValueError(f"应为{main_count}个号码，抓到 {len(numbers)} 个: {numbers}")
    return {
        "draw_date": draw_date, "numbers": numbers,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
    }


def fetch_digits(url: str, digit_count: int, session: str) -> dict:
    soup = _fetch_soup(url)
    row = soup.select_one(".c-draw-card")
    if row is None:
        raise ValueError("没找到 .c-draw-card")
    date_el = row.select_one(".c-draw-card__draw-date-sub")
    draw_date = _parse_date(date_el.get_text(strip=True))
    balls = row.select("ul.c-draw-card__ball-list li.c-ball")
    # Wild Ball (bonus) is also a li.c-ball but wrapped in .c-result__bonus — exclude it from digits.
    bonus_container = row.select_one(".c-result__bonus")
    bonus_ball = bonus_container.select_one(".c-ball") if bonus_container else None
    digit_balls = [b for b in balls if bonus_ball is None or b is not bonus_ball]
    digits = [b.get_text(strip=True) for b in digit_balls]
    if len(digits) != digit_count:
        raise ValueError(f"应为{digit_count}位数字，抓到 {len(digits)} 个: {digits}")
    wild_ball = bonus_ball.get_text(strip=True) if bonus_ball else None
    return {
        "draw_date": draw_date, "session": session, "digits": digits, "wild_ball": wild_ball,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
    }


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
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
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
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    errors = []

    pool_games = [
        ("cash5", "https://www.lotteryusa.com/pennsylvania/cash-5/", 5),
        ("match6", "https://www.lotteryusa.com/pennsylvania/match-6/", 6),
        ("treasure-hunt", "https://www.lotteryusa.com/pennsylvania/treasure-hunt/", 5),
    ]
    for subdir, url, count in pool_games:
        try:
            result = fetch_pool(url, count)
            log.info("%s: %s", subdir, result)
            save_pool(subdir, result)
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))
        time.sleep(1.5)

    digit_games = [
        ("pick2", 2, "https://www.lotteryusa.com/pennsylvania/midday-pick-2/", "https://www.lotteryusa.com/pennsylvania/pick-2/"),
        ("pick3", 3, "https://www.lotteryusa.com/pennsylvania/midday-pick-3/", "https://www.lotteryusa.com/pennsylvania/pick-3/"),
        ("pick4", 4, "https://www.lotteryusa.com/pennsylvania/midday-pick-4/", "https://www.lotteryusa.com/pennsylvania/pick-4/"),
        ("pick5", 5, "https://www.lotteryusa.com/pennsylvania/midday-pick-5/", "https://www.lotteryusa.com/pennsylvania/pick-5/"),
    ]
    for subdir, count, day_url, night_url in digit_games:
        try:
            result = fetch_digits(day_url, count, "day")
            log.info("%s day: %s", subdir, result)
            save_digits(subdir, result)
        except Exception as e:  # noqa: BLE001
            log.error("%s day 失败: %s", subdir, e)
            errors.append((f"{subdir}-day", e))
        time.sleep(1.5)  # lotteryusa.com rate-limited GitHub Actions' IP with zero-delay back-to-back requests (real incident, not a guess)
        try:
            result = fetch_digits(night_url, count, "evening")
            log.info("%s evening: %s", subdir, result)
            save_digits(subdir, result)
        except Exception as e:  # noqa: BLE001
            log.error("%s evening 失败: %s", subdir, e)
            errors.append((f"{subdir}-evening", e))
        time.sleep(1.5)

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
