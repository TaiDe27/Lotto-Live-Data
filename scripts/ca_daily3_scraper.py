"""
California Daily 3 抓取 — calottery.com 官方站直接抓

合规说明、UA选择理由同 ca_superlotto_plus_scraper.py，这里不重复。

Daily 3 一天两期(Midday 1pm左右 + Evening 6:30pm左右，均为太平洋时间)，
官方页面把两期的开奖卡片放在同一个 #drawGame9 容器里依次排列(先Evening后
Midday)，用"THU/OCT 8, 2026 - EVENING"/"... - MIDDAY"这种带session后缀的
日期文本区分，不是分开两个URL——跟Arkansas那边(两个独立页面)不一样，这里
解析时按"每个 .draw-cards--draw-date 紧跟着的 .draw-cards--winning-numbers
号码列表"配对处理。
"""

import dataclasses
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ca_daily3_scraper")

def iso_date(raw: str) -> str:
    """"WED/OCT 7, 2026" -> "2026-10-07" — calottery.com's own date text, normalized to the
    same ISO yyyy-MM-dd shape every other scraper in this repo already emits."""
    import datetime as _dt
    return _dt.datetime.strptime(raw, "%a/%b %d, %Y").strftime("%Y-%m-%d")

URL = "https://www.calottery.com/en/draw-games/daily-3"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class Daily3Result:
    game: str
    draw_date: str  # "THU/OCT 8, 2026" (session后缀已剥离，单独放 session 字段)
    session: str  # "midday" | "evening"
    draw_number: str
    digits: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> list[Daily3Result]:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            card = soup.select_one("[id^='drawGame']")
            if not card:
                raise ValueError("页面里没找到 drawGame 开奖卡片")

            results = []
            for date_p in card.select(".draw-cards--draw-date"):
                raw_text = date_p.get_text(strip=True)
                m = re.match(r"(.+?)\s*-\s*(EVENING|MIDDAY)", raw_text, re.I)
                if not m:
                    continue
                draw_date, session_label = iso_date(m.group(1).strip()), m.group(2).upper()
                session = "evening" if session_label == "EVENING" else "midday"

                num_p = date_p.find_next_sibling("p", class_="draw-cards--draw-number")
                draw_number = num_p.get_text(strip=True).replace("Draw #", "").strip() if num_p else ""

                ball_list = date_p.find_next_sibling("ul", class_="draw-cards--winning-numbers")
                if ball_list is None:
                    continue
                digits = [int(s.get_text(strip=True)) for s in ball_list.select("span.draw-cards--winning-numbers-inner-wrapper")]

                results.append(Daily3Result(
                    game="Daily 3",
                    draw_date=draw_date,
                    session=session,
                    draw_number=draw_number,
                    digits=digits,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    source_url=URL,
                ))

            if not results:
                raise ValueError("解析出的 Daily 3 记录为空")
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: Daily3Result) -> None:
    if len(result.digits) != 3:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位，预期 3 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


MAX_HISTORY = 100


def save_results(results: list[Daily3Result]) -> Path:
    out_dir = OUTPUT_DIR / "california" / "daily-3"
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
