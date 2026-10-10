"""
Colorado Pick 3 抓取 — coloradolottery.com 官方站直接抓

合规说明同 co_lotto_plus_scraper.py，这里不重复。

一天两期(Midday+Evening)，官网把两期放在同一个页面里，详情链接 href 直接带
"yyyy-MM-dd:MD" / "yyyy-MM-dd:EV" 这种 ISO日期+session后缀格式(如
/en/games/pick3/drawings/2026-10-09:MD/)，不需要解析"Friday, 10/9"这种文字
日期——直接从 URL 里取，比 Arkansas/California 那几个 Pick3/4 脚本更简单。
"""

import dataclasses
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("co_pick3_scraper")

URL = "https://www.coloradolottery.com/en/games/pick3/"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SESSION_MAP = {"MD": "midday", "EV": "evening"}


@dataclasses.dataclass
class Pick3Result:
    game: str
    draw_date: str
    session: str  # "midday" | "evening"
    digits: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> list[Pick3Result]:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            links = soup.select('a.panel[href*="/games/pick3/drawings/"]')
            if not links:
                raise ValueError("页面里没找到任何开奖详情链接")

            results = []
            for link in links:
                m = re.search(r"/drawings/(\d{4}-\d{2}-\d{2}):(MD|EV)/", link.get("href", ""))
                if not m:
                    continue
                draw_date, session_code = m.group(1), m.group(2)
                balls = link.select(".draw .drawNumber")
                digits = [int(b.get_text(strip=True)) for b in balls]
                results.append(Pick3Result(
                    game="Pick 3",
                    draw_date=draw_date,
                    session=SESSION_MAP[session_code],
                    digits=digits,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    source_url=URL,
                ))

            if not results:
                raise ValueError("解析出的 Pick 3 记录为空")
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: Pick3Result) -> None:
    if len(result.digits) != 3:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


MAX_HISTORY = 100


def save_results(results: list[Pick3Result]) -> Path:
    out_dir = OUTPUT_DIR / "colorado" / "pick3"
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
    new_keys = {(e["draw_date"], e["session"]) for e in new_entries}
    history = [h for h in history if (h.get("draw_date"), h.get("session")) not in new_keys]

    session_rank = {"midday": 0, "evening": 1}
    merged = new_entries + history
    merged.sort(key=lambda e: (e["draw_date"], session_rank.get(e.get("session"), -1)), reverse=True)
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    for r in results:
        validate(r)
    log.info("抓取结果: %s", [r.to_dict() for r in results])
    save_results(results)


if __name__ == "__main__":
    main()
