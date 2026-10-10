"""
Delaware Play 3 抓取 — delottery.com 官方站直接抓

合规说明：delottery.com 的 robots.txt 完全放行，官网没有独立的"Terms of
Use"页面（只有 Privacy Policy，里面没有任何禁止自动化访问的条款）——跟
Powerball.com/Florida/California的情况类似，没有发现禁止爬虫的条款。

⚠️ Play 3/4/5 各自页面上有个"Prizes & Odds"分 Tab，内容其实是一张payout图表
**图片**（/Content/images/drawing-games-play-three-payout-chart.jpg），不是
文字表格——HTML里拿不到具体奖金数字。这份脚本因此没有去抓这个"图表"，奖金
金额用的是 Pick3 全国通行的标准固定奖金（Straight $500 / 6-way box $80 /
3-way box $160，$1投注），写在 App 端的 games.json 里，不是从这个页面抓来的
——如果官方实际金额跟这个不一样，需要人工核对。

Play 3 一天两期(Day ~1:58pm ET + Night ~7:57pm ET)，页面把两期放在同一个
URL里(用 <i class="li li-sun...">/<i class="li li-lg li-moon"> 图标区分)，
日期格式是"MM/DD/YYYY"，解析时转成ISO。
"""

import dataclasses
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("de_play5_scraper")

URL = "https://www.delottery.com/Drawing-Games/Play-5"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
DIGIT_COUNT = 5
GAME_NAME = "Play 5"
OUT_SUBDIR = "play-5"


def iso_date(raw: str) -> str:
    """"10/09/2026" -> "2026-10-09"."""
    return dt.datetime.strptime(raw, "%m/%d/%Y").strftime("%Y-%m-%d")


OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class PlayResult:
    game: str
    draw_date: str
    session: str  # "day" | "night"
    digits: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> list[PlayResult]:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            results = []
            for icon_class, session in (("li-sun", "day"), ("li-moon", "night")):
                icon = soup.select_one(f"i.{icon_class}")
                if icon is None:
                    continue
                header = icon.find_parent("h4")
                if header is None:
                    continue
                date_text = header.get_text(strip=True).lstrip("- ").strip()
                m = re.search(r"\d{2}/\d{2}/\d{4}", date_text)
                if not m:
                    continue
                draw_date = iso_date(m.group(0))

                ball_box = header.find_next("div", class_="drawing-ball")
                if ball_box is None:
                    continue
                digits = [int(d.get_text(strip=True)) for d in ball_box.find_all("div")]

                results.append(PlayResult(
                    game=GAME_NAME,
                    draw_date=draw_date,
                    session=session,
                    digits=digits,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    source_url=URL,
                ))

            if not results:
                raise ValueError("页面里没找到任何 Day/Night 开奖数据")
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: PlayResult) -> None:
    if len(result.digits) != DIGIT_COUNT:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位，预期 {DIGIT_COUNT} 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


MAX_HISTORY = 100


def save_results(results: list[PlayResult]) -> Path:
    out_dir = OUTPUT_DIR / "delaware" / OUT_SUBDIR
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

    session_rank = {"day": 0, "night": 1}
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
