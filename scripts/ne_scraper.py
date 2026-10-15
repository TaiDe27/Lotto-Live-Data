"""
Nebraska own-games — nelottery.com's real winning-numbers pages are at `/lotto-detail?gamename=...`
(found via the site's own `/lotto-results` nav grid — a bare `/homeapp/lotto/<slug>` guess 404s).
Each page server-renders a `.numbertable` history table (newest row first), one row per draw date.

Pick 5 / Pick 3 / Pick 4: each draws exactly ONE session per day (confirmed from the table having
one row per date, not two) — unlike most other states' Pick 3/4.

MyDaY (Nebraska-exclusive): NOT a pool-of-same-range-numbers game — players pick a calendar date
(month 1-12, day 1-31, 2-digit year 00-99), confirmed from nelottery.com's own results table having
separate "Month"/"Day"/"Year" columns rather than one combined number list. Modeled on the Swift
side as `Game.isDateFieldsPick` (display-only; see that property's doc comment for why matching/
entry/OCR aren't modeled yet for this one mechanic) — `numbers` here is `[month, day, year]` in that
fixed order, matching `Drawing.mainNumbers`'s expected shape for this game.
"""
import dataclasses
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ne_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "nebraska"
MAX_HISTORY = 100


def _get(url: str) -> str:
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


def fetch_latest_row(gamename: str, expected_cols: int) -> tuple[str, list[int]]:
    from bs4 import BeautifulSoup

    url = f"https://nelottery.com/lotto-detail?gamename={gamename.replace(' ', '+')}"
    html = _get(url)
    soup = BeautifulSoup(html, "html.parser")
    # Some game pages (Pick 4) also have an unrelated "Bet Type"/payout-rules table that happens to
    # share the bare `numbertable` class — the real results history table always additionally has
    # `ld_table`, so select on both classes together to avoid grabbing the wrong one.
    table = soup.select_one("table.numbertable.ld_table")
    if table is None:
        raise ValueError("没找到 table.numbertable.ld_table")
    rows = table.select("tbody tr")
    if not rows:
        raise ValueError("表格里没有数据行")
    first_row = rows[0]
    cells = first_row.select("td")
    if len(cells) < 2:
        raise ValueError(f"数据行列数不够: {len(cells)}")

    date_text = cells[0].get_text(strip=True)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_text)
    if not m:
        raise ValueError(f"日期格式不对: {date_text!r}")
    mm, dd, yyyy = m.groups()
    draw_date = f"{yyyy}-{int(mm):02d}-{int(dd):02d}"

    values: list[int] = []
    for cell in cells[1:]:
        text = cell.get_text(strip=True)
        if "," in text:
            values.extend(int(v.strip()) for v in text.split(","))
        else:
            values.append(int(text))

    if len(values) != expected_cols:
        raise ValueError(f"应为{expected_cols}个数字，抓到 {len(values)} 个: {values}")
    return draw_date, values


def save_results(subdir: str, draw_date: str, numbers: list[int], source_url: str) -> Path:
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
    new_entry = {
        "draw_date": draw_date,
        "numbers": numbers,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_url": source_url,
    }
    history = [h for h in history if h.get("draw_date") != draw_date]
    merged = ([new_entry] + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


GAMES = [
    ("Pick 5", "pick5", 5),
    ("Pick 3", "pick3", 3),
    ("Pick 4", "pick4", 4),
    ("MyDaY", "myday", 3),
]


def main():
    errors = []
    for gamename, subdir, expected_cols in GAMES:
        try:
            draw_date, numbers = fetch_latest_row(gamename, expected_cols)
            url = f"https://nelottery.com/lotto-detail?gamename={gamename.replace(' ', '+')}"
            log.info("%s: draw_date=%s numbers=%s", subdir, draw_date, numbers)
            save_results(subdir, draw_date, numbers, url)
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))
    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
