"""
Tri-State Megabucks Plus（mainelottery.com 首页）抓取

这是 Maine/New Hampshire/Vermont 三州共用同一期开奖的跨州游戏，按本项目已有的
7 款跨州游戏同一套架构处理（一个共享的 Game 定义 + 一份 latest.json，不是
按州各自建一份）。首页本身就是服务端直接渲染好真实数据的（本session已直接
验证），不需要走 lotteryusa.com。

玩法：选5个主号(1-41) + 1个Megaball(1-6)。页面结构里每个游戏是一个
<div class="clearfix row">块，Tri-State Megabucks Plus 用 gamelogo 里的
alt="Tri-State Megabucks Plus" 识别，而不是按页面顺序猜测位置。
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
log = logging.getLogger("tristate_megabucks_scraper")

URL = "https://www.mainelottery.com/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
MAX_HISTORY = 100

OUTPUT_DIR = Path("data")


def iso_date(raw: str) -> str:
    # raw like "Wednesday 10/14/2026"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if not m:
        raise ValueError(f"unparseable date: {raw!r}")
    month, day, year = m.groups()
    return dt.date(int(year), int(month), int(day)).isoformat()


@dataclasses.dataclass
class TriStateMegabucksResult:
    draw_date: str
    main_numbers: list[int]
    megaball: Optional[int]
    jackpot_amount: Optional[str]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> list[TriStateMegabucksResult]:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            logo_img = soup.select_one('img[alt="Tri-State Megabucks Plus"]')
            if not logo_img:
                raise ValueError("Tri-State Megabucks Plus 的 gamelogo 没找到")
            row = logo_img.find_parent("div", class_="clearfix row") or logo_img.find_parent("div")
            if row is None:
                raise ValueError("找不到 Tri-State Megabucks Plus 所在的结果区块")

            date_el = row.select_one(".homewinners h2")
            if not date_el:
                raise ValueError("找不到开奖日期")
            draw_date = iso_date(date_el.get_text(strip=True))

            balls = row.select(".win-numbers .ball span")
            main_numbers = [int(b.get_text(strip=True)) for b in balls]

            rball = row.select_one(".win-numbers .rball span")
            megaball = int(rball.get_text(strip=True)) if rball else None

            jackpot_el = row.select_one(".homejackpot .cash")
            jackpot_amount = jackpot_el.get_text(strip=True) if jackpot_el else None

            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            result = TriStateMegabucksResult(
                draw_date=draw_date,
                main_numbers=main_numbers,
                megaball=megaball,
                jackpot_amount=jackpot_amount,
                fetched_at=now_iso,
                source_url=URL,
            )
            return [result]
        except Exception as e:
            last_err = e
            log.warning("attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC)
    raise RuntimeError(f"全部重试失败: {last_err}")


def merge_history(new_results: list[TriStateMegabucksResult], out_path: Path) -> list[dict]:
    existing: list[dict] = []
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
        except Exception:
            existing = []

    by_date = {r["draw_date"]: r for r in existing}
    for r in new_results:
        by_date[r.draw_date] = r.to_dict()

    merged = sorted(by_date.values(), key=lambda r: r["draw_date"], reverse=True)
    return merged[:MAX_HISTORY]


def main():
    results = fetch()
    log.info("抓到 %d 条真实结果: %s", len(results), results[0].draw_date if results else None)

    out_dir = OUTPUT_DIR / "multistate" / "tri-state-megabucks-plus"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "latest.json"

    merged = merge_history(results, out_path)
    out_path.write_text(json.dumps(merged, indent=2))
    log.info("写入 %s，共 %d 条历史记录", out_path, len(merged))


if __name__ == "__main__":
    main()
