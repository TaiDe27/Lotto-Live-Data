"""
Colorado Cash 5 抓取 — coloradolottery.com 官方站直接抓

合规说明同 co_lotto_plus_scraper.py，这里不重复。

规则：5个号码从1-32，无特殊球，固定奖($20,000顶奖，不滚存)。页面结构、ISO
日期提取方式都跟 Lotto+ 一样。
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
log = logging.getLogger("co_cash5_scraper")

URL = "https://www.coloradolottery.com/en/games/cash5/"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class Cash5Result:
    game: str
    draw_date: str
    numbers: list[int]
    jackpot_amount: Optional[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> Cash5Result:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            link = soup.select_one('a[href*="/games/cash5/drawings/"]')
            if not link:
                raise ValueError("页面里没找到最新一期的开奖详情链接")
            m = re.search(r"/drawings/(\d{4}-\d{2}-\d{2})/", link.get("href", ""))
            if not m:
                raise ValueError(f"开奖详情链接格式不对: {link.get('href')}")
            draw_date = m.group(1)

            panel = link if "panel" in (link.get("class") or []) else link.find_parent("a", class_="panel")
            if panel is None:
                panel = soup.select_one("a.panel")
            balls = panel.select(".draw .drawNumber")[:5]
            numbers = [int(b.get_text(strip=True)) for b in balls]

            jackpot_amount = None
            jackpot_el = panel.select_one(".drawJackpot")
            if jackpot_el:
                m2 = re.search(r"\$([\d,]+)", jackpot_el.get_text())
                if m2:
                    jackpot_amount = int(m2.group(1).replace(",", ""))

            if len(numbers) != 5:
                raise ValueError(f"号码应为5个，抓到 {len(numbers)} 个: {numbers}")

            return Cash5Result(
                game="Cash 5",
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


def validate(result: Cash5Result) -> None:
    if len(result.numbers) != 5:
        raise ValueError(f"号码应为5个: {result.numbers}")
    if not all(1 <= n <= 32 for n in result.numbers):
        raise ValueError(f"号码超出 1-32 范围: {result.numbers}")


MAX_HISTORY = 100


def save_results(result: Cash5Result) -> Path:
    out_dir = OUTPUT_DIR / "colorado" / "cash5"
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
