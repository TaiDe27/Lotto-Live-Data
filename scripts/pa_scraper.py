"""
Pennsylvania own-games — migrated 2026-10-16 from lotteryusa.com to lottery.net entirely (one
source for the whole state), per explicit user decision. palottery.pa.gov/.state.pa.us are both
still confirmed hard-blocked at the TLS handshake itself (SSL_ERROR_SYSCALL) — this is still a
third-party source, not the official site, same class of fallback as Montana/NC/TN/OK's
lotteryusa.com usage, just a different aggregator for PA specifically.

`robots.txt` only disallows `/en/play/` and `/change/` — every game page used here is unaffected.

Confirmed real mechanics (quoted from lottery.net's own PA game pages, re-verified independently
of the prior lotteryusa.com-sourced quotes, not just carried over):
  - Cash 5: "The Cash 5 drawing is held every evening at 6:59 PM ET, seven days a week." 5 of 1-43.
  - Match 6 Lotto: "With drawings every evening at 6:59 PM ET..." 6 of 1-49.
  - Treasure Hunt: "...can be won every day during the 1:35 PM ET draw." 5 of 1-30.
  - Pick 2/3/4/5: "There are two daily draws – the Day draw takes place at 1:35 PM ET, and the
    Evening draw is held at 6:59 PM ET." Each with a Wild Ball add-on digit (`li.wild-ball`,
    separate from the `li.number-part-0N` pick digits). UNLIKE lotteryusa.com (which needed two
    separate URLs for day vs. evening), lottery.net's single `/pennsylvania/pick-N/numbers` page
    shows BOTH sessions for the same date in one fetch (two `.drawTime` blocks) — one request now
    covers what used to take two.
  - Cash Pop: 4 named sessions in ONE page (`/pennsylvania/cash-pop/numbers`), each its own
    `.balls.cash-pop` block: Morning (9:00 AM ET), Matinee (1:35 PM ET), Afternoon (6:59 PM ET),
    Late Night (11:00 PM ET) — confirmed from the page's own FAQ text ("Morning Buzz at 9:00 AM
    ET, Lunch Break at 1:35 PM ET, Prime Time at 6:59 PM ET and Night Owl at 11:00 PM ET") and
    cross-checked against the results table's own session labels/times, which use slightly
    different display names (Morning/Matinee/Afternoon/Late Night) for the same 4 clock times.
    Pool is 1-15 (standard membershipPick shape, same mechanic as every other state's Cash POP in
    this project — one number drawn, real prize is a per-ticket multiplier the public data can't
    reveal). "Pennsylvania Wild Ball" (`/pennsylvania/wild-ball`) is NOT a 9th game — verified it's
    just an info/FAQ page about the Pick-games' Wild Ball add-on itself.

Each game writes its own `data/pennsylvania/<subdir>/latest.json` — subdir names and output JSON
field names for the 7 pre-existing games (`draw_date`/`numbers`, or
`draw_date`/`session`/`digits`/`wild_ball`) are UNCHANGED from the prior lotteryusa.com-sourced
version, so `BundledDataSource.swift`/remote config.json's `liveMapping` field references don't
need updating for them. Cash Pop's 4 sessions are NEW this round and follow the modern pattern
already established for South Carolina/Missouri/North Carolina/Mississippi's Cash POP — one
`draw_date`/`session`/`number` (singular, string) per game subdir, NOT folded into a single
multi-session file (that older shape is specific to the Swift-hardcoded Indiana/Maine/Maryland
path, which newer states no longer use — remote config.json's `liveMapping` with per-game
`anchorHour`/`anchorMinute` is the real live-fetch path now).
"""
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pa_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "pennsylvania"
MAX_HISTORY = 100
BASE = "https://www.lottery.net/pennsylvania"


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


def _first_result_box(soup):
    box = soup.select_one(".resultBox.firstResult")
    if box is None:
        raise ValueError("没找到 .resultBox.firstResult")
    return box


def fetch_pool(slug: str, main_count: int) -> dict:
    url = f"{BASE}/{slug}/numbers"
    soup = _fetch_soup(url)
    box = _first_result_box(soup)
    date_el = box.select_one(".resultDate")
    draw_date = _parse_date(date_el.get_text(" ", strip=True))
    balls = box.select("ul.balls li.ball")
    numbers = [int(b.get_text(strip=True)) for b in balls]
    if len(numbers) != main_count:
        raise ValueError(f"应为{main_count}个号码，抓到 {len(numbers)} 个: {numbers}")
    return {
        "draw_date": draw_date, "numbers": numbers,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
    }


def fetch_digits_both_sessions(slug: str, digit_count: int) -> list:
    """Pick-N pages show Day + Evening in ONE fetch (two `.drawTime` blocks)."""
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
        session = "day" if label.get_text(strip=True).lower() == "day" else "evening"
        digit_lis = draw_time_el.select("ul.balls li[class*='number-part']")
        wild_li = draw_time_el.select_one("ul.balls li.wild-ball")
        digits = [li.get_text(strip=True) for li in digit_lis]
        if len(digits) != digit_count:
            raise ValueError(f"{slug} {session} 应为{digit_count}位数字，抓到 {len(digits)} 个: {digits}")
        wild_ball = wild_li.get_text(strip=True) if wild_li else None
        results.append({
            "draw_date": draw_date, "session": session, "digits": digits, "wild_ball": wild_ball,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
        })
    if len(results) != 2:
        raise ValueError(f"{slug}: 应为2个场次(day/evening)，抓到 {len(results)} 个")
    return results


# cashPopName (display label on lottery.net's results table) -> our session key, matching the
# FAQ-confirmed real clock times: Morning=9:00AM, Matinee=1:35PM, Afternoon=6:59PM, Late Night=11:00PM.
CASH_POP_SESSION_MAP = {
    "morning": "morning", "matinee": "matinee", "afternoon": "afternoon", "late night": "latenight",
}


def fetch_cash_pop() -> list:
    url = f"{BASE}/cash-pop/numbers"
    soup = _fetch_soup(url)
    box = _first_result_box(soup)
    date_el = box.select_one(".resultDate")
    draw_date = _parse_date(date_el.get_text(" ", strip=True))
    results = []
    for session_block in box.select(".cashPopContainer .balls.cash-pop"):
        name_el = session_block.select_one(".cashPopName")
        ball_el = session_block.select_one(".ball")
        if name_el is None or ball_el is None:
            continue
        label = name_el.get_text(strip=True).lower()
        session = CASH_POP_SESSION_MAP.get(label)
        if session is None:
            raise ValueError(f"未知的 Cash Pop 场次名: {label!r}")
        number = ball_el.get_text(strip=True)
        results.append({
            "draw_date": draw_date, "session": session, "number": number,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
        })
    if len(results) != 4:
        raise ValueError(f"Cash Pop: 应为4个场次，抓到 {len(results)} 个")
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


def save_cash_pop(session: str, new_entry: dict) -> Path:
    out_dir = OUTPUT_DIR / f"cashpop-{session}"
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


def main():
    errors = []

    pool_games = [
        ("cash5", "cash-5", 5),
        ("match6", "match-6-lotto", 6),
        ("treasure-hunt", "treasure-hunt", 5),
    ]
    for subdir, slug, count in pool_games:
        try:
            result = fetch_pool(slug, count)
            log.info("%s: %s", subdir, result)
            save_pool(subdir, result)
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))
        time.sleep(1.0)

    digit_games = [("pick2", "pick-2", 2), ("pick3", "pick-3", 3), ("pick4", "pick-4", 4), ("pick5", "pick-5", 5)]
    for subdir, slug, count in digit_games:
        try:
            for result in fetch_digits_both_sessions(slug, count):
                log.info("%s %s: %s", subdir, result["session"], result)
                save_digits(subdir, result)
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))
        time.sleep(1.0)

    try:
        for result in fetch_cash_pop():
            log.info("cashpop %s: %s", result["session"], result)
            save_cash_pop(result["session"], result)
    except Exception as e:  # noqa: BLE001
        log.error("cashpop 失败: %s", e)
        errors.append(("cashpop", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
