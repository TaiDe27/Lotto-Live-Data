"""
Montana own-games — montanalottery.com's official site is confirmed hard-blocked by a genuine
Cloudflare Managed Challenge ("Just a moment..." page) on every plain HTTP request regardless of
headers (re-verified directly this round: full browser-realistic headers still get the challenge
page, not a real response) — this requires actual JS execution to solve, which this project's
plain-HTTP scrapers architecturally can't do, and browser automation was explicitly ruled out as
impractical here (GitHub Actions' datacenter IPs likely get flagged regardless of browser fidelity,
Managed Challenges are inherently unreliable for scheduled automation). Per this project's existing
precedent for other Cloudflare-blocked states (NC/TN/OK/PA), this uses lotteryusa.com instead —
confirmed clean, server-rendered `.c-draw-card` results table, no JS needed.

Big Sky Bonus: 4 of 1-31 + a separate Bonus Number (1-16) — confirmed from lotteryusa.com's own
rendering (a `.c-result__bonus` list item tagged "BN"), NOT a plain 5-number pick as an earlier,
uncorroborated page capture had assumed.
Montana Cash: 5 of 1-45, no bonus number.
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
log = logging.getLogger("mt_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "montana"
MAX_HISTORY = 100


@dataclasses.dataclass
class MtResult:
    draw_date: str
    numbers: list[int]
    bonus_number: Optional[int]
    next_jackpot_amount: Optional[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch_one(url: str, main_count: int, has_bonus: bool) -> MtResult:
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
                raise ValueError("没找到 .c-draw-card（可能被 Cloudflare 拦截了）")

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
            main_balls = ball_list.select("li.c-ball")
            numbers = [int(b.get_text(strip=True)) for b in main_balls]
            if len(numbers) != main_count:
                raise ValueError(f"应为{main_count}个主号码，抓到 {len(numbers)} 个: {numbers}")

            bonus_number = None
            if has_bonus:
                bonus_el = ball_list.select_one("li.c-result__bonus .c-ball")
                if bonus_el is None:
                    raise ValueError("没找到 Bonus Number")
                bonus_number = int(bonus_el.get_text(strip=True))

            next_jackpot = None
            prize_el = row.select_one(".c-draw-card__prize-value")
            if prize_el:
                m2 = re.search(r"\$([\d,]+)", prize_el.get_text())
                if m2:
                    next_jackpot = int(m2.group(1).replace(",", ""))

            return MtResult(
                draw_date=draw_date, numbers=numbers, bonus_number=bonus_number,
                next_jackpot_amount=next_jackpot,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), source_url=url,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def save_results(subdir: str, result: MtResult) -> Path:
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
    new_entry = result.to_dict()
    history = [h for h in history if h.get("draw_date") != new_entry["draw_date"]]
    merged = ([new_entry] + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    errors = []
    try:
        result = fetch_one("https://www.lotteryusa.com/montana/big-sky-bonus/", main_count=4, has_bonus=True)
        log.info("big-sky-bonus: %s", result.to_dict())
        save_results("big-sky-bonus", result)
    except Exception as e:  # noqa: BLE001
        log.error("big-sky-bonus 失败: %s", e)
        errors.append(("big-sky-bonus", e))

    try:
        result = fetch_one("https://www.lotteryusa.com/montana/montana-cash/", main_count=5, has_bonus=False)
        log.info("montana-cash: %s", result.to_dict())
        save_results("montana-cash", result)
    except Exception as e:  # noqa: BLE001
        log.error("montana-cash 失败: %s", e)
        errors.append(("montana-cash", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
