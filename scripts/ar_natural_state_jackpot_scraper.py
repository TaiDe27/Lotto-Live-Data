"""
Arkansas Natural State Jackpot 抓取 — lotteryusa.com

数据来源合规说明同 ar_lotto_scraper.py，这里不重复。

NSJ 规则很简单：5个号码从1-39，没有Bonus/特殊球，每天开一次。
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
log = logging.getLogger("ar_natural_state_jackpot_scraper")

def iso_date(raw: str) -> str:
    """"Oct 7, 2026" -> "2026-10-07" — lotteryusa.com's own date text, normalized to the same
    ISO yyyy-MM-dd shape every other scraper in this repo already emits, so the App's existing
    `parseLocalDate`/`DateRule` machinery (and `LiveMappingRule`'s own "yyyy-MM-dd" dateFormat)
    can parse this without a bespoke format just for these four scrapers."""
    import datetime as _dt
    return _dt.datetime.strptime(raw, "%b %d, %Y").strftime("%Y-%m-%d")

URL = "https://www.lotteryusa.com/arkansas/natural-state-jackpot/"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MONEY_PATTERN = r"\$?([\d,.]+\s*(?:Million|Billion|million|billion)?)"


@dataclasses.dataclass
class NSJResult:
    game: str
    draw_date: str
    numbers: list[int]  # 5个
    jackpot_amount: Optional[str]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def parse_money(raw: str) -> Optional[str]:
    if not raw:
        return None
    m = re.search(MONEY_PATTERN, raw)
    return m.group(1).strip() if m else None


def fetch() -> list[NSJResult]:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("tr.c-draw-card")
            if not rows:
                raise ValueError("页面里没找到任何 c-draw-card 开奖记录行")

            results = []
            for row in rows:
                date_el = row.select_one(".c-draw-card__draw-date-sub")
                if not date_el:
                    continue
                draw_date = iso_date(date_el.get_text(strip=True))

                balls = row.select(".c-draw-card__ball-list > li.c-ball")
                numbers = [int(b.get_text(strip=True)) for b in balls]

                prize_el = row.select_one(".c-draw-card__prize-value")
                jackpot_amount = parse_money(prize_el.get_text(strip=True)) if prize_el else None

                results.append(NSJResult(
                    game="Natural State Jackpot",
                    draw_date=draw_date,
                    numbers=numbers,
                    jackpot_amount=jackpot_amount,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    source_url=URL,
                ))
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: NSJResult) -> None:
    if len(result.numbers) != 5:
        raise ValueError(f"号码应为5个，抓到 {len(result.numbers)} 个: {result.numbers}")
    if not all(1 <= n <= 39 for n in result.numbers):
        raise ValueError(f"号码超出 1-39 范围: {result.numbers}")


MAX_HISTORY = 100


def save_results(results: list[NSJResult]) -> Path:
    out_dir = OUTPUT_DIR / "arkansas" / "natural-state-jackpot"
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

    new_entries = [r.to_dict() for r in results]
    new_keys = {e["draw_date"] for e in new_entries}
    history = [h for h in history if h.get("draw_date") not in new_keys]

    merged = new_entries + history
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    for r in results[:3]:
        validate(r)
    log.info("抓取结果(最新3条): %s", [r.to_dict() for r in results[:3]])
    save_results(results)


if __name__ == "__main__":
    main()
