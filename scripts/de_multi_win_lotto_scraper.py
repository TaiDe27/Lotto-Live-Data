"""
Delaware Multi-Win Lotto 抓取 — delottery.com 官方站直接抓

合规说明同 de_play3_scraper.py，这里不重复。

Multi-Win Lotto：6个号码从1-35，无特殊球，每天开一次(晚7:57pm ET)。页面
主体就直接显示"Current top prize"金额，不用再去翻payout图片——这个游戏的
jackpot金额本身就是明文文本，不是图片。日期格式是"Friday, October 09,
2026"这种完整英文格式。

跟Play 3/4/5一样，详细的全部奖级payout表在"Prizes & Odds"分Tab里，同样是
图片(不是文字)，没有去抓——奖级金额用games.json里按官方"Current top
prize"区间($50,000起步，滚存)+常见Multi-Win Lotto奖级结构配置，不是从这个
页面精确抓来的。
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
log = logging.getLogger("de_multi_win_lotto_scraper")

URL = "https://www.delottery.com/Drawing-Games/Multi-Win-Lotto"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def iso_date(raw: str) -> str:
    """"Friday, October 09, 2026" -> "2026-10-09"."""
    return dt.datetime.strptime(raw, "%A, %B %d, %Y").strftime("%Y-%m-%d")


@dataclasses.dataclass
class MultiWinLottoResult:
    game: str
    draw_date: str
    numbers: list[int]
    jackpot_amount: Optional[str]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> MultiWinLottoResult:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            date_el = soup.select_one(".drawing-ball").find_previous("p")
            draw_date = iso_date(date_el.get_text(strip=True))

            ball_box = soup.select_one(".drawing-ball")
            numbers = [int(d.get_text(strip=True)) for d in ball_box.find_all("div")]

            jackpot_el = soup.find("strong")
            jackpot_amount = jackpot_el.get_text(strip=True) if jackpot_el else None

            if not numbers or not draw_date:
                raise ValueError(f"解析出的数据不完整: numbers={numbers} draw_date={draw_date}")

            return MultiWinLottoResult(
                game="Multi-Win Lotto",
                draw_date=draw_date,
                numbers=numbers,
                jackpot_amount=jackpot_amount,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: MultiWinLottoResult) -> None:
    if len(result.numbers) != 6:
        raise ValueError(f"号码应为6个，抓到 {len(result.numbers)} 个: {result.numbers}")
    if not all(1 <= n <= 35 for n in result.numbers):
        raise ValueError(f"号码超出 1-35 范围: {result.numbers}")


MAX_HISTORY = 100


def save_results(result: MultiWinLottoResult) -> Path:
    out_dir = OUTPUT_DIR / "delaware" / "multi-win-lotto"
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
    merged = [new_entry] + history
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    result = fetch()
    validate(result)
    log.info("抓取结果: %s", result.to_dict())
    save_results(result)


if __name__ == "__main__":
    main()
