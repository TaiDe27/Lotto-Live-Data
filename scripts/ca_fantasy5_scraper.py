"""
California Fantasy 5 抓取 — calottery.com 官方站直接抓

合规说明、UA选择理由同 ca_superlotto_plus_scraper.py，这里不重复。

Fantasy 5 规则：5个号码从1-39，没有特殊球。页面结构(开奖卡片 + Prize
breakdown表格)也跟SuperLotto Plus一样，只是没有Mega号这一块。
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
log = logging.getLogger("ca_fantasy5_scraper")

URL = "https://www.calottery.com/en/draw-games/fantasy-5"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class TierResult:
    label: str
    winners: int
    prize: str


@dataclasses.dataclass
class Fantasy5Result:
    game: str
    draw_date: str
    draw_number: Optional[str]
    numbers: list[int]
    tiers: list[dict]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> Fantasy5Result:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            card = soup.select_one("[id^='winningNumbers']")
            if not card:
                raise ValueError("页面里没找到 winningNumbers 开奖卡片")

            date_el = card.select_one(".draw-cards--draw-date")
            draw_date = date_el.get_text(strip=True) if date_el else None
            num_el = card.select_one(".draw-cards--draw-number")
            draw_number = num_el.get_text(strip=True).replace("Draw #", "") if num_el else None

            balls = card.select("span.draw-cards--winning-numbers-inner-wrapper")
            numbers = [int(b.get_text(strip=True)) for b in balls]

            tiers = []
            table = soup.select_one("table.table-last-draw")
            if table:
                for row in table.select("tbody tr"):
                    if "total" in row.get("class", []):
                        continue
                    cells = [c.get_text(strip=True) for c in row.select("td")]
                    if len(cells) != 3:
                        continue
                    label, winners_text, prize = cells
                    winners = int(re.sub(r"[^\d]", "", winners_text) or 0)
                    tiers.append(dataclasses.asdict(TierResult(label=label, winners=winners, prize=prize)))

            if not numbers or not draw_date:
                raise ValueError(f"解析出的数据不完整: numbers={numbers} draw_date={draw_date}")

            return Fantasy5Result(
                game="Fantasy 5",
                draw_date=draw_date,
                draw_number=draw_number,
                numbers=numbers,
                tiers=tiers,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: Fantasy5Result) -> None:
    if len(result.numbers) != 5:
        raise ValueError(f"号码应为5个，抓到 {len(result.numbers)} 个: {result.numbers}")
    if not all(1 <= n <= 39 for n in result.numbers):
        raise ValueError(f"号码超出 1-39 范围: {result.numbers}")


MAX_HISTORY = 100


def save_results(result: Fantasy5Result) -> Path:
    out_dir = OUTPUT_DIR / "california" / "fantasy-5"
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
