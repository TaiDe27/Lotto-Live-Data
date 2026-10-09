"""
California Daily Derby 抓取 — calottery.com 官方站直接抓

合规说明、UA选择理由同 ca_superlotto_plus_scraper.py，这里不重复。

⚠️ Daily Derby 是赛马风格游戏，不是数字/球号玩法：开奖结果是"第一/二/三名
是几号马、叫什么名字"+"比赛用时"，玩家也是下注"押中第一/二/三名的马"或者
"押中比赛用时"，彻底不是 mainNumbers/specialNumbers 那一套。这个脚本只负责
把官方页面上的事实数据(名次、马号、马名、比赛用时、各奖级中奖情况)抓下来
存成 latest.json——App 那边要不要/怎么把这种"非数字"玩法接进现有的
NumberBallView/NumberMatchingService渲染体系，是另一个需要单独设计的
工程任务(跟 Powerball Xs & Os 当初用 winningTeams 字段绕开 mainNumbers 的
思路类似，但赛马比"8选8支队伍"更复杂，还多了名次和比赛用时两个维度)，
不在这份抓取脚本的范围内。
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
log = logging.getLogger("ca_daily_derby_scraper")

URL = "https://www.calottery.com/en/draw-games/daily-derby"
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
class DailyDerbyResult:
    game: str
    draw_date: str
    draw_number: Optional[str]
    first_place: str  # 原样保留，如 "03 - Hot Shot"
    second_place: str
    third_place: str
    race_time: Optional[str]  # 如 "1:43.00"
    tiers: list[dict]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> DailyDerbyResult:
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
            draw_number = num_el.get_text(strip=True).replace("Draw #", "").strip() if num_el else None

            places = {"first": None, "second": None, "third": None}
            race_time = None
            for li in card.select("ul.draw-cards--derby-winners li"):
                text = re.sub(r"\s+", " ", li.get_text(strip=True))
                m = re.match(r"(First|Second|Third):\s*(.+)", text, re.I)
                if m:
                    places[m.group(1).lower()] = m.group(2).strip()
                    continue
                m2 = re.match(r"Race Time:\s*(.+)", text, re.I)
                if m2:
                    race_time = m2.group(1).strip()

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

            if not draw_date or not all(places.values()):
                raise ValueError(f"解析出的数据不完整: draw_date={draw_date} places={places}")

            return DailyDerbyResult(
                game="Daily Derby",
                draw_date=draw_date,
                draw_number=draw_number,
                first_place=places["first"],
                second_place=places["second"],
                third_place=places["third"],
                race_time=race_time,
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


def validate(result: DailyDerbyResult) -> None:
    if not (result.first_place and result.second_place and result.third_place):
        raise ValueError(f"名次信息不完整: {result.first_place} / {result.second_place} / {result.third_place}")


MAX_HISTORY = 100


def save_results(result: DailyDerbyResult) -> Path:
    out_dir = OUTPUT_DIR / "california" / "daily-derby"
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
