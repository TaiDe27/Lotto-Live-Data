"""
Minnesota own-games — mnlottery.com's own game pages render the latest winning numbers server-side
(confirmed: curling /games/lotto/gopher-5, /games/lotto/north-5, /games/lotto/pick-3 with a plain
browser UA returns a `<ul class="lottery-number-list">` with the real numbers already in the HTML,
no JS execution needed). mnlottery.com also runs a separate "GameOn" widget
(widget.gameon.mnlottery.com) for second-chance-drawing logins only — that one's gateway API is
NOT the winning-numbers source, a dead end checked and ruled out before finding the real pattern
above.

North 5 draws daily; Gopher 5 draws Mon/Wed/Fri; Pick 3 draws daily, ONE session per day (no
midday/evening split, unlike most other states' Pick 3 — confirmed from the same page, which shows
exactly one `lottery-number-list` block).
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
log = logging.getLogger("mn_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "minnesota"
MAX_HISTORY = 100

GAMES = [
    # (url, output subdir, field name for numbers, expected count)
    ("https://www.mnlottery.com/games/lotto/gopher-5", "gopher5", 5),
    ("https://www.mnlottery.com/games/lotto/north-5", "north5", 5),
    ("https://www.mnlottery.com/games/lotto/pick-3", "pick3", 3),
]


@dataclasses.dataclass
class MnResult:
    draw_date: str  # yyyy-MM-dd
    numbers: list[int]
    next_jackpot_amount: Optional[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch_one(url: str, expected_count: int) -> MnResult:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            card = soup.select_one(".card--lottery")
            if card is None:
                raise ValueError("页面里没找到 .card--lottery 开奖卡片")

            items = card.select(".lottery-number-list-item")
            numbers = [int(li.get_text(strip=True)) for li in items]
            if len(numbers) != expected_count:
                raise ValueError(f"应为 {expected_count} 个号码，抓到 {len(numbers)} 个: {numbers}")

            drawing_text = card.select_one(".lottery-drawing")
            if drawing_text is None:
                raise ValueError("没找到 .lottery-drawing 开奖日期")
            m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2})", drawing_text.get_text())
            if not m:
                raise ValueError(f"开奖日期格式不对: {drawing_text.get_text()!r}")
            mm, dd, yy = m.groups()
            draw_date = f"20{yy}-{int(mm):02d}-{int(dd):02d}"

            next_jackpot = None
            jackpot_el = card.select_one(".lottery-payout")
            if jackpot_el:
                m2 = re.search(r"\$([\d,]+)", jackpot_el.get_text())
                if m2:
                    next_jackpot = int(m2.group(1).replace(",", ""))

            return MnResult(
                draw_date=draw_date,
                numbers=numbers,
                next_jackpot_amount=next_jackpot,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=url,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def save_results(subdir: str, result: MnResult) -> Path:
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
    for url, subdir, expected_count in GAMES:
        try:
            result = fetch_one(url, expected_count)
            log.info("%s 抓取结果: %s", subdir, result.to_dict())
            save_results(subdir, result)
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))
    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
