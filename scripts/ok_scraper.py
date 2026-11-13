"""
Oklahoma own-games — oklottery.com's official site is confirmed hard-blocked by AWS CloudFront WAF
(403 on every request including robots.txt, re-verified this round, no page content obtainable at
all) — per explicit user decision, this uses lotteryusa.com instead, same fallback pattern as
Montana/North Carolina/Tennessee.

Only 2 OK-exclusive own-games exist (confirmed from lotteryusa.com's own Oklahoma game list — the
rest of OK's catalog is multistate games already covered elsewhere in this project):
  - Cash 5: 5 of 1-36, $1/play, daily 9:00 PM CT (Oklahoma local time).
  - Pick 3: standard digit game, daily 9:00 PM CT, no add-on digit mentioned on lotteryusa.com.

2026-11-13: added next_draw_date/next_jackpot_amount for Cash 5 — confirmed live on
lotteryusa.com/oklahoma/cash-5/'s own `.c-state-short-info` "Next draw" widget ("Next Cash 5 draw"
-> "Today at 9:00 pm CST", "Next top prize" -> "$25,000"), real published forward-looking data, not
a guess. Parsed via the shared `lotteryusa_shared.parse_next_draw_info` helper already used by other
lotteryusa.com-sourced states this session.
"""
import dataclasses
import datetime as dt
import json
import logging
import re
import sys
import time
import zoneinfo
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import lotteryusa_shared as lu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ok_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "oklahoma"
MAX_HISTORY = 100

# Pick 3 is single-session only — confirmed via play.lottery.ok.gov/play/draw/pick3 ("Tickets may
# be purchased until 8:59 pm the evening of the drawing, and drawings are held nightly after 9:00
# P.M.") and the official lottery.ok.gov Pick 3 page (same wording). No midday session exists;
# don't re-add one without a new official citation.
OK_TZ = zoneinfo.ZoneInfo("America/Chicago")
OK_PICK3_DRAW_HOUR = 21  # 9:00 PM CT


def compute_ok_pick3_next_draw_date() -> str:
    now = dt.datetime.now(OK_TZ)
    next_draw = now.replace(hour=OK_PICK3_DRAW_HOUR, minute=0, second=0, microsecond=0)
    if now >= next_draw:
        next_draw += dt.timedelta(days=1)
    return next_draw.date().isoformat()


@dataclasses.dataclass
class OkResult:
    draw_date: str
    numbers: list
    fetched_at: str
    source_url: str
    next_draw_date: Optional[str] = None
    next_jackpot_amount: Optional[str] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch_one(url: str, main_count: int) -> OkResult:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            row = soup.select_one(".c-draw-card")
            if row is None:
                raise ValueError("没找到 .c-draw-card")

            date_el = row.select_one(".c-draw-card__draw-date-sub")
            if date_el is None:
                raise ValueError("没找到开奖日期")
            m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})", date_el.get_text(strip=True))
            if not m:
                raise ValueError(f"日期格式不对: {date_el.get_text()!r}")
            month_name, day, year = m.groups()
            month = dt.datetime.strptime(month_name[:3], "%b").month
            draw_date = dt.date(int(year), month, int(day)).isoformat()

            ball_list = row.select_one("ul.c-draw-card__ball-list")
            if ball_list is None:
                raise ValueError("没找到 .c-draw-card__ball-list")
            balls = ball_list.select("li.c-ball")
            numbers = [int(b.get_text(strip=True)) for b in balls]
            if len(numbers) != main_count:
                raise ValueError(f"应为{main_count}个号码，抓到 {len(numbers)} 个: {numbers}")

            next_info = lu.parse_next_draw_info(resp.text)

            return OkResult(
                draw_date=draw_date, numbers=numbers,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), source_url=url,
                next_draw_date=next_info.get("next_draw_date"),
                next_jackpot_amount=next_info.get("next_jackpot_amount"),
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def fetch_digits(url: str, digit_count: int) -> dict:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            row = soup.select_one(".c-draw-card")
            if row is None:
                raise ValueError("没找到 .c-draw-card")
            date_el = row.select_one(".c-draw-card__draw-date-sub")
            m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})", date_el.get_text(strip=True))
            month_name, day, year = m.groups()
            month = dt.datetime.strptime(month_name[:3], "%b").month
            draw_date = dt.date(int(year), month, int(day)).isoformat()
            ball_list = row.select_one("ul.c-draw-card__ball-list")
            balls = ball_list.select("li.c-ball")
            digits = [b.get_text(strip=True) for b in balls]
            if len(digits) != digit_count:
                raise ValueError(f"应为{digit_count}位数字，抓到 {len(digits)} 个: {digits}")
            return {
                "draw_date": draw_date, "digits": digits,
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
            }
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def save_results(subdir: str, new_entry: dict, key_field: str = "draw_date") -> Path:
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
    history = [h for h in history if h.get(key_field) != new_entry.get(key_field)]
    merged = ([new_entry] + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    errors = []
    try:
        result = fetch_one("https://www.lotteryusa.com/oklahoma/cash-5/", main_count=5)
        log.info("cash5: %s", result.to_dict())
        save_results("cash5", result.to_dict())
    except Exception as e:  # noqa: BLE001
        log.error("cash5 失败: %s", e)
        errors.append(("cash5", e))

    try:
        result = fetch_digits("https://www.lotteryusa.com/oklahoma/pick-3/", digit_count=3)
        result["next_draw_date"] = compute_ok_pick3_next_draw_date()
        log.info("pick3: %s", result)
        save_results("pick3", result)
    except Exception as e:  # noqa: BLE001
        log.error("pick3 失败: %s", e)
        errors.append(("pick3", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
